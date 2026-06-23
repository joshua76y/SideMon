#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <TFT_eSPI.h>
#include <ArduinoJson.h>
#include <math.h>

#ifndef SIDEMON_DEFAULT_SSID
#define SIDEMON_DEFAULT_SSID "SSID"
#endif
#ifndef SIDEMON_DEFAULT_PASS
#define SIDEMON_DEFAULT_PASS "PASSWORD"
#endif
#ifndef SIDEMON_DEFAULT_PORT
#define SIDEMON_DEFAULT_PORT 9877
#endif

static const uint16_t W = 320;
static const uint16_t H = 240;

static const uint16_t COL_BG       = 0x080C;
static const uint16_t COL_CARD     = 0x1823;
static const uint16_t COL_PANEL    = 0x1016;
static const uint16_t COL_BORDER   = 0x2E3A;
static const uint16_t COL_TEXT     = 0xF0F4;
static const uint16_t COL_TEXT2    = 0xA8B8;
static const uint16_t COL_TEXT3    = 0x6480;
static const uint16_t COL_SYS      = 0x0D69;
static const uint16_t COL_API      = 0x32A0;
static const uint16_t COL_CLASH    = 0xFA82;
static const uint16_t COL_CODEX    = 0xAA64;
static const uint16_t COL_WEATHER  = 0xFC82;
static const uint16_t COL_DATETIME = 0x4FC0;
static const uint16_t COL_OMLX     = 0x4FD8;
static const uint16_t COL_WHITE    = 0xFFFF;
static const uint16_t COL_RED      = 0xD882;
static const uint16_t COL_GREEN    = 0x0D69;
static const uint16_t COL_YELLOW   = 0xFF00;
static const uint16_t COL_ORANGE   = 0xF400;

static const uint16_t PAGE_BGS[] = {
    0x092E, 0x0926, 0x1608, 0x1206, 0x161A, 0x0932, 0x0A28
};
static const uint16_t PAGE_ACCENTS[] = {
    COL_SYS, COL_API, COL_CLASH, COL_CODEX, COL_WEATHER, COL_DATETIME, COL_OMLX
};

static const size_t MAX_JSON = 4096;

TFT_eSPI tft = TFT_eSPI();

struct AppState {
  bool wifiReady = false;
  bool hasData = false;
  unsigned long lastRender = 0;
  unsigned long lastDiscovery = 0;
  unsigned long lastReconnect = 0;
  size_t activeCount = 1;
  size_t currentPage = 0;
  uint16_t tcpPort = SIDEMON_DEFAULT_PORT;
  char pageKeys[8][12];
  JsonDocument doc;
  WiFiClient client;
  WiFiUDP udp;
  char rxLine[MAX_JSON];
  size_t rxLen = 0;
  bool rxOverrun = false;
};

AppState app;

static uint16_t colorFromPercent(float pct) {
  if (pct < 0.50f) return COL_GREEN;
  if (pct < 0.75f) return COL_YELLOW;
  if (pct < 0.90f) return COL_ORANGE;
  return COL_RED;
}

static uint16_t blendColor(uint16_t base, uint8_t alpha) {
  uint8_t rb = ((base >> 11) & 0x1F);
  uint8_t gb = ((base >> 5) & 0x3F);
  uint8_t bb = (base & 0x1F);
  rb = (rb * alpha) >> 8;
  gb = (gb * alpha) >> 8;
  bb = (bb * alpha) >> 8;
  return ((rb & 0x1F) << 11) | ((gb & 0x3F) << 5) | (bb & 0x1F);
}

static void safeCopy(char* dst, size_t cap, const char* src) {
  if (!src || !cap) return;
  size_t n = strlen(src);
  if (n >= cap) n = cap - 1;
  memcpy(dst, src, n);
  dst[n] = 0;
}

static void resetAppState() {
  app.hasData = false;
  app.activeCount = 1;
  app.currentPage = 0;
  app.rxLen = 0;
  app.rxOverrun = false;
  safeCopy(app.pageKeys[0], sizeof(app.pageKeys[0]), "system");
  for (size_t i = 1; i < 8; ++i) app.pageKeys[i][0] = 0;
}

static void normalizePages(JsonArray pages) {
  static const char* known[] = {"system", "ccswitch", "clash", "codex", "weather", "datetime", "omlx"};
  size_t idx = 0;
  if (!pages.isNull()) {
    for (JsonVariant v : pages) {
      const char* key = v.as<const char*>();
      if (!key) continue;
      bool valid = false;
      for (auto k : known) {
        if (strcmp(k, key) == 0) { valid = true; break; }
      }
      if (!valid) continue;
      bool dup = false;
      for (size_t j = 0; j < idx; ++j) {
        if (strcmp(app.pageKeys[j], key) == 0) { dup = true; break; }
      }
      if (!dup && idx < 8) {
        safeCopy(app.pageKeys[idx], sizeof(app.pageKeys[idx]), key);
        ++idx;
      }
    }
  }
  if (idx == 0) {
    for (auto k : known) {
      safeCopy(app.pageKeys[idx], sizeof(app.pageKeys[idx]), k);
      if (++idx >= 7) break;
    }
  }
  app.activeCount = idx;
  if (app.currentPage >= app.activeCount) app.currentPage = 0;
}

static void drawHeader(uint8_t pageIdx, const char* title, uint16_t accent, uint16_t bg) {
  tft.fillScreen(bg);
  tft.fillRect(0, 0, W, 28, COL_PANEL);
  tft.fillRect(0, 0, 5, 28, accent);
  tft.setTextColor(accent, COL_PANEL);
  tft.setTextSize(2);
  tft.setCursor(12, 6);
  tft.print(title);
  tft.setTextColor(COL_TEXT3, COL_PANEL);
  tft.setTextSize(1);
  tft.setCursor(W - 40, 10);
  char tmp[6];
  snprintf(tmp, sizeof(tmp), "%u/%u", pageIdx + 1, (unsigned)app.activeCount);
  tft.print(tmp);
}

static void drawCard(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t fill) {
  tft.fillRoundRect(x, y, w, h, 8, fill);
  tft.drawRoundRect(x, y, w, h, 8, COL_BORDER);
}

static void drawLabelValue(uint16_t x, uint16_t y, const char* label, const char* value, uint16_t valueColor) {
  tft.setTextColor(COL_TEXT3, COL_CARD);
  tft.setTextSize(1);
  tft.setCursor(x, y);
  tft.print(label);
  tft.setTextColor(valueColor, COL_CARD);
  tft.setTextSize(2);
  tft.setCursor(x, y + 14);
  tft.print(value);
}

static void drawBar(uint16_t x, uint16_t y, uint16_t w, uint16_t h, float pct, uint16_t fg) {
  uint16_t bg = blendColor(COL_BORDER, 200);
  tft.fillRoundRect(x, y, w, h, h / 2, bg);
  uint16_t fw = max((uint16_t)4, (uint16_t)(w * constrain(pct, 0.0f, 1.0f)));
  tft.fillRoundRect(x, y, fw, h, h / 2, fg);
}

static void drawArc(uint16_t cx, uint16_t cy, uint16_t r, float pct, uint16_t fg, uint16_t bg, uint8_t thick) {
  tft.drawCircle(cx, cy, r, bg);
  if (thick > 1) {
    tft.drawCircle(cx, cy, r - 1, bg);
    if (thick > 2) tft.drawCircle(cx, cy, r + 1, bg);
  }
  if (pct <= 0.002f) return;
  float endDeg = 225.0f - 360.0f * min(pct, 1.0f);
  if (endDeg < 0.0f) endDeg += 360.0f;
  for (float deg = 225.0f; deg != endDeg; deg += (endDeg > 225.0f ? -1.0f : -1.0f)) {
    float a = deg * DEG_TO_RAD;
    int16_t px = cx + (int16_t)(cos(a) * r);
    int16_t py = cy - (int16_t)(sin(a) * r);
    tft.fillCircle(px, py, thick, fg);
  }
}

static JsonObject getPage(const char* key) {
  JsonVariant v = app.doc[key];
  if (v.is<JsonObject>()) return v.as<JsonObject>();
  return JsonObject();
}

static void fmtBigUnits(char* out, size_t cap, long value) {
  if (value >= 1000000000L) snprintf(out, cap, "%.1fB", value / 1000000000.0);
  else if (value >= 1000000L) snprintf(out, cap, "%.1fM", value / 1000000.0);
  else if (value >= 1000L) snprintf(out, cap, "%.1fK", value / 1000.0);
  else snprintf(out, cap, "%ld", value);
}

static void renderSystem(uint8_t idx) {
  JsonObject s = getPage("system");
  uint16_t bg = PAGE_BGS[0];
  drawHeader(idx, "SYSTEM", PAGE_ACCENTS[0], bg);

  float cpu = s["cpu"] | 0.0f;
  float mem = s["mem"] | 0.0f;
  float disk = s["disk"] | 0.0f;
  char c1[8], c2[8], c3[8];
  snprintf(c1, sizeof(c1), "%d%%", (int)roundf(cpu));
  snprintf(c2, sizeof(c2), "%d%%", (int)roundf(mem));
  snprintf(c3, sizeof(c3), "%d%%", (int)roundf(disk));

  uint16_t radius = 36;
  uint16_t cx1 = 52, cx2 = 160, cx3 = 268, cy = 82;
  drawArc(cx1, cy, radius, cpu / 100.0f, colorFromPercent(cpu / 100.0f), COL_BORDER, 5);
  drawArc(cx2, cy, radius, mem / 100.0f, colorFromPercent(mem / 100.0f), COL_BORDER, 5);
  drawArc(cx3, cy, radius, disk / 100.0f, colorFromPercent(disk / 100.0f), COL_BORDER, 5);

  tft.setTextColor(COL_TEXT, bg);
  tft.setTextSize(2);
  tft.setCursor(cx1 - 18, cy - 8); tft.print(c1);
  tft.setCursor(cx2 - 18, cy - 8); tft.print(c2);
  tft.setCursor(cx3 - 18, cy - 8); tft.print(c3);

  tft.setTextColor(PAGE_ACCENTS[0], bg);
  tft.setTextSize(1);
  tft.setCursor(cx1 - 10, cy + radius + 8); tft.print("CPU");
  tft.setCursor(cx2 - 12, cy + radius + 8); tft.print("MEM");
  tft.setCursor(cx3 - 14, cy + radius + 8); tft.print("DISK");

  uint16_t cw = (W - 40) / 3;
  drawCard(10, 156, cw, 48, COL_CARD);
  drawCard(20 + cw, 156, cw, 48, COL_CARD);
  drawCard(30 + 2 * cw, 156, cw, 48, COL_CARD);

  const char* uptime = s["uptime"] | "?";
  drawLabelValue(18, 162, "UPTIME", uptime, PAGE_ACCENTS[0]);

  JsonArray load = s["load"];
  char loadText[16];
  if (!load.isNull() && load.size() >= 2) snprintf(loadText, sizeof(loadText), "%.1f %.1f", load[0].as<float>(), load[1].as<float>());
  else snprintf(loadText, sizeof(loadText), "0.0 0.0");
  drawLabelValue(28 + cw, 162, "LOAD", loadText, COL_TEXT2);

  const char* host = s["hostname"] | "?";
  char shortHost[14];
  safeCopy(shortHost, sizeof(shortHost), host);
  drawLabelValue(38 + 2 * cw, 162, "HOST", shortHost, COL_TEXT2);
}

static void renderApis(uint8_t idx) {
  JsonObject api = getPage("ccswitch");
  uint16_t bg = PAGE_BGS[1];
  drawHeader(idx, "API USAGE", PAGE_ACCENTS[1], bg);

  const char* ds = api["ds_balance"] | "?";
  float dsVal = atof(ds);
  float dsPct = dsVal > 0 ? min(dsVal / 50.0f, 1.0f) : 0.0f;
  drawCard(10, 36, W - 20, 46, COL_CARD);
  tft.setTextColor(PAGE_ACCENTS[1], COL_CARD); tft.setTextSize(2); tft.setCursor(18, 42); tft.print("DeepSeek");
  drawBar(18, 62, W - 150, 8, dsPct, PAGE_ACCENTS[1]);
  char dsValS[16]; snprintf(dsValS, sizeof(dsValS), "CNY %.1f", dsVal);
  tft.setTextColor(COL_TEXT, COL_CARD); tft.setTextSize(2); tft.setCursor(W - 120, 56); tft.print(dsValS);

  const char* mm = api["mm_balance"] | "0";
  float mmPct = atof(mm) / 100.0f;
  drawCard(10, 90, W - 20, 46, COL_CARD);
  tft.setTextColor(PAGE_ACCENTS[3], COL_CARD); tft.setTextSize(2); tft.setCursor(18, 96); tft.print("MiMo");
  drawBar(18, 116, W - 150, 8, mmPct, PAGE_ACCENTS[3]);
  char mmS[16]; snprintf(mmS, sizeof(mmS), "%.1f%%", mmPct * 100.0f);
  tft.setTextColor(COL_TEXT, COL_CARD); tft.setTextSize(2); tft.setCursor(W - 100, 110); tft.print(mmS);

  uint16_t cw = (W - 40) / 3;
  drawCard(10, 146, cw, 54, COL_CARD);
  drawCard(20 + cw, 146, cw, 54, COL_CARD);
  drawCard(30 + 2 * cw, 146, cw, 54, COL_CARD);
  char t1[16], t2[16], t3[16];
  fmtBigUnits(t1, sizeof(t1), api["total_tokens"] | 0L);
  fmtBigUnits(t2, sizeof(t2), api["output_tokens"] | 0L);
  snprintf(t3, sizeof(t3), "%.1f%%", api["cache_hit_rate"] | 0.0f);
  drawLabelValue(18, 152, "TOTAL", t1, COL_TEXT);
  drawLabelValue(28 + cw, 152, "OUTPUT", t2, COL_GREEN);
  drawLabelValue(38 + 2 * cw, 152, "CACHE", t3, COL_WEATHER);
}

static void renderClash(uint8_t idx) {
  JsonObject cl = getPage("clash");
  uint16_t bg = PAGE_BGS[2];
  drawHeader(idx, "CLASH", PAGE_ACCENTS[2], bg);

  bool online = cl["running"] | false;
  tft.fillRoundRect(W - 70, 8, 60, 18, 6, online ? COL_GREEN : COL_RED);
  tft.setTextColor(COL_BG, online ? COL_GREEN : COL_RED); tft.setTextSize(1); tft.setCursor(W - 60, 12);
  tft.print(online ? "ONLINE" : "OFFLINE");

  const char* node = cl["current_node"] | "?";
  const char* mode = cl["mode"] | "Rule";
  uint16_t cw = (W - 30) / 2;
  drawCard(10, 36, cw, 42, COL_CARD);
  drawLabelValue(18, 42, "NODE", node, COL_TEXT);
  drawCard(20 + cw, 36, cw, 42, COL_CARD);
  drawLabelValue(28 + cw, 42, "MODE", mode, PAGE_ACCENTS[3]);

  const char* tu = cl["traffic_used"] | "N/A";
  const char* tt = cl["traffic_total"] | "";
  drawCard(10, 86, W - 20, 48, COL_CARD);
  tft.setTextColor(COL_TEXT3, COL_CARD); tft.setTextSize(1); tft.setCursor(18, 92); tft.print("TRAFFIC");
  char traffic[32];
  if (strlen(tt)) snprintf(traffic, sizeof(traffic), "%s / %s", tu, tt);
  else snprintf(traffic, sizeof(traffic), "%s", tu);
  tft.setTextColor(COL_TEXT, COL_CARD); tft.setTextSize(2); tft.setCursor(18, 108); tft.print(traffic);

  uint16_t cw3 = (W - 40) / 3;
  drawCard(10, 142, cw3, 46, COL_CARD);
  drawCard(20 + cw3, 142, cw3, 46, COL_CARD);
  drawCard(30 + 2 * cw3, 142, cw3, 46, COL_CARD);
  const char* expire = cl["expire_date"] | "N/A";
  const char* version = cl["version"] | "?";
  char conns[8]; snprintf(conns, sizeof(conns), "%d", cl["active_connections"] | 0);
  drawLabelValue(18, 148, "EXPIRE", expire, COL_WEATHER);
  drawLabelValue(28 + cw3, 148, "VERSION", version, COL_TEXT2);
  drawLabelValue(38 + 2 * cw3, 148, "CONNS", conns, PAGE_ACCENTS[2]);

  const char* update = cl["update_time"] | "";
  tft.setTextColor(COL_TEXT3, bg); tft.setTextSize(1); tft.setCursor(18, 200); tft.printf("Updated: %s", update);
}

static void renderCodex(uint8_t idx) {
  JsonObject cx = getPage("codex");
  uint16_t bg = PAGE_BGS[3];
  drawHeader(idx, "CODEX", PAGE_ACCENTS[3], bg);

  float p5 = cx["pct_5h"] | 0.0f;
  float p7 = cx["pct_7d"] | 0.0f;
  uint16_t cx1 = 100, cx2 = 220, cy = 96, r = 52;
  drawArc(cx1, cy, r, p5 / 100.0f, colorFromPercent(p5 / 100.0f), COL_BORDER, 8);
  drawArc(cx2, cy, r, p7 / 100.0f, colorFromPercent(p7 / 100.0f), COL_BORDER, 8);

  char p5s[8], p7s[8];
  snprintf(p5s, sizeof(p5s), "%d%%", (int)roundf(p5));
  snprintf(p7s, sizeof(p7s), "%d%%", (int)roundf(p7));
  tft.setTextColor(COL_TEXT, bg); tft.setTextSize(2);
  tft.setCursor(cx1 - 18, cy - 8); tft.print(p5s);
  tft.setCursor(cx2 - 18, cy - 8); tft.print(p7s);

  tft.setTextColor(PAGE_ACCENTS[3], bg); tft.setTextSize(2);
  tft.setCursor(cx1 - 30, cy + r + 10); tft.print("5 HOUR");
  tft.setCursor(cx2 - 24, cy + r + 10); tft.print("7 DAY");

  drawCard(10, 180, W - 20, 42, COL_CARD);
  const char* r5 = cx["reset_5h"] | "?";
  const char* r7 = cx["reset_7d"] | "?";
  tft.setTextColor(COL_TEXT3, COL_CARD); tft.setTextSize(1); tft.setCursor(18, 186); tft.print("RESET 5H");
  tft.setTextColor(PAGE_ACCENTS[3], COL_CARD); tft.setTextSize(2); tft.setCursor(18, 198); tft.print(r5);
  tft.setTextColor(COL_TEXT3, COL_CARD); tft.setTextSize(1); tft.setCursor(W / 2 + 8, 186); tft.print("RESET 7D");
  tft.setTextColor(PAGE_ACCENTS[3], COL_CARD); tft.setTextSize(2); tft.setCursor(W / 2 + 8, 198); tft.print(r7);
}

static void renderWeather(uint8_t idx) {
  JsonObject w = getPage("weather");
  uint16_t bg = PAGE_BGS[4];
  drawHeader(idx, "WEATHER", PAGE_ACCENTS[4], bg);

  const char* city = w["city"] | "?";
  const char* cond = w["condition"] | "?";
  const char* temp = w["temperature"] | "?";
  drawCard(10, 36, W - 20, 68, COL_CARD);
  tft.setTextColor(PAGE_ACCENTS[4], COL_CARD); tft.setTextSize(2); tft.setCursor(18, 42); tft.print(city);
  tft.setTextColor(COL_TEXT, COL_CARD); tft.setTextSize(2); tft.setCursor(18, 66); tft.print(cond);
  tft.setTextColor(COL_WHITE, COL_CARD); tft.setTextSize(3); tft.setCursor(W - 110, 50); tft.printf("%sC", temp);

  uint16_t cw = (W - 30) / 2;
  drawCard(10, 112, cw, 46, COL_CARD);
  drawCard(20 + cw, 112, cw, 46, COL_CARD);
  char hum[16], wind[16];
  snprintf(hum, sizeof(hum), "%d%%", w["humidity"] | 0);
  snprintf(wind, sizeof(wind), "%d km/h", w["wind_speed"] | 0);
  drawLabelValue(18, 118, "HUMIDITY", hum, COL_API);
  drawLabelValue(28 + cw, 118, "WIND", wind, COL_TEXT2);

  drawCard(10, 166, W - 20, 42, COL_CARD);
  const char* f1 = w["forecast_1"] | "";
  const char* f2 = w["forecast_2"] | "";
  char fline[48];
  snprintf(fline, sizeof(fline), "%s  |  %s", f1, f2);
  tft.setTextColor(COL_TEXT2, COL_CARD); tft.setTextSize(2); tft.setCursor(18, 176); tft.print(fline);
}

static void renderDatetime(uint8_t idx) {
  JsonObject dt = getPage("datetime");
  uint16_t bg = PAGE_BGS[5];
  drawHeader(idx, "DATETIME", PAGE_ACCENTS[5], bg);

  time_t ts = dt["timestamp"] | 0;
  struct tm tmv;
  localtime_r(&ts, &tmv);
  char timeS[8], dateS[12], dow[12];
  strftime(timeS, sizeof(timeS), "%H:%M", &tmv);
  strftime(dateS, sizeof(dateS), "%Y-%m-%d", &tmv);
  strftime(dow, sizeof(dow), "%A", &tmv);

  drawCard(10, 36, 148, 66, COL_CARD);
  tft.setTextColor(COL_WHITE, COL_CARD); tft.setTextSize(4); tft.setCursor(20, 44); tft.print(timeS);
  drawCard(168, 36, 142, 66, COL_CARD);
  tft.setTextColor(COL_YELLOW, COL_CARD); tft.setTextSize(2); tft.setCursor(178, 44); tft.print(dateS);
  tft.setTextColor(PAGE_ACCENTS[5], COL_CARD); tft.setTextSize(2); tft.setCursor(178, 70); tft.print(dow);

  drawCard(10, 112, W - 20, 96, COL_CARD);
  int wd = tmv.tm_wday;
  int md = tmv.tm_mday;
  int first = md - ((wd + 6) % 7);
  int daysInMonth = 30;
  if (tmv.tm_mon == 0 || tmv.tm_mon == 2 || tmv.tm_mon == 4 || tmv.tm_mon == 6 || tmv.tm_mon == 7 || tmv.tm_mon == 9 || tmv.tm_mon == 11) daysInMonth = 31;
  else if (tmv.tm_mon == 1) daysInMonth = 28;
  uint16_t colW = (W - 40) / 7;
  tft.setTextColor(PAGE_ACCENTS[5], COL_CARD); tft.setTextSize(2); tft.setCursor(90, 118); tft.printf("%04d-%02d", tmv.tm_year + 1900, tmv.tm_mon + 1);
  const char* labels[7] = {"Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"};
  for (int i = 0; i < 7; ++i) {
    tft.setTextColor(i == 6 ? COL_RED : COL_TEXT2, COL_CARD); tft.setTextSize(1); tft.setCursor(16 + i * colW, 138); tft.print(labels[i]);
  }
  int day = 1;
  for (int row = 0; row < 5 && day <= daysInMonth; ++row) {
    for (int col = 0; col < 7 && day <= daysInMonth; ++col) {
      int x = 16 + col * colW;
      int y = 154 + row * 18;
      if (day == md) {
        tft.fillRoundRect(x - 3, y - 2, colW - 6, 16, 4, PAGE_ACCENTS[5]);
        tft.setTextColor(COL_BG, PAGE_ACCENTS[5]); tft.setTextSize(1); tft.setCursor(x, y); tft.printf("%2d", day);
      } else {
        tft.setTextColor(col == 6 ? COL_RED : (col == 5 ? COL_ORANGE : COL_TEXT2), COL_CARD);
        tft.setTextSize(1); tft.setCursor(x, y); tft.printf("%2d", day);
      }
      ++day;
    }
  }
}

static void renderOmlx(uint8_t idx) {
  JsonObject om = getPage("omlx");
  uint16_t bg = PAGE_BGS[6];
  drawHeader(idx, "OMLX", PAGE_ACCENTS[6], bg);

  bool online = om["running"] | false;
  tft.fillRoundRect(W - 70, 8, 60, 18, 6, online ? COL_GREEN : COL_RED);
  tft.setTextColor(COL_BG, online ? COL_GREEN : COL_RED); tft.setTextSize(1); tft.setCursor(W - 60, 12);
  tft.print(online ? "ONLINE" : "OFFLINE");

  drawCard(10, 36, W - 20, 44, COL_CARD);
  float mu = om["memory_used"] | 0.0f;
  float mc = om["memory_ceiling"] | 1.0f;
  tft.setTextColor(COL_TEXT3, COL_CARD); tft.setTextSize(1); tft.setCursor(18, 42); tft.print("MEMORY");
  tft.setTextColor(COL_TEXT2, COL_CARD); tft.setTextSize(2); tft.setCursor(100, 40); tft.printf("%.1f / %.1f GB", mu, mc);
  drawBar(18, 60, W - 40, 8, mc > 0 ? mu / mc : 0.0f, PAGE_ACCENTS[6]);

  uint16_t hw = (W - 30) / 2;
  drawCard(10, 90, hw, 44, COL_CARD);
  drawCard(20 + hw, 90, hw, 44, COL_CARD);
  char models[16], reqs[16];
  snprintf(models, sizeof(models), "%d/%d", om["loaded_count"] | 0, om["model_count"] | 0);
  fmtBigUnits(reqs, sizeof(reqs), om["total_requests"] | 0L);
  drawLabelValue(18, 96, "MODELS", models, PAGE_ACCENTS[6]);
  drawLabelValue(28 + hw, 96, "REQUESTS", reqs, COL_TEXT);

  drawCard(10, 142, hw, 44, COL_CARD);
  drawCard(20 + hw, 142, hw, 44, COL_CARD);
  char promptS[16], genS[16];
  snprintf(promptS, sizeof(promptS), "%.1f tk/s", om["avg_prompt_speed"] | 0.0f);
  snprintf(genS, sizeof(genS), "%.1f tk/s", om["avg_gen_speed"] | 0.0f);
  drawLabelValue(18, 148, "PROMPT", promptS, COL_API);
  drawLabelValue(28 + hw, 148, "GEN", genS, COL_WEATHER);

  drawCard(10, 194, W - 20, 30, COL_CARD);
  float ce = (om["cache_efficiency"] | 0.0f) * 100.0f;
  tft.setTextColor(COL_TEXT3, COL_CARD); tft.setTextSize(1); tft.setCursor(18, 200); tft.print("CACHE");
  drawBar(100, 202, W - 140, 8, ce / 100.0f, PAGE_ACCENTS[6]);
}

static void renderWaiting() {
  uint16_t bg = PAGE_BGS[0];
  tft.fillScreen(bg);
  tft.fillRect(0, 0, W, 28, COL_PANEL);
  tft.fillRect(0, 0, 5, 28, COL_SYS);
  tft.setTextColor(COL_SYS, COL_PANEL); tft.setTextSize(2); tft.setCursor(12, 6); tft.print("SIDEMON");
  tft.setTextColor(COL_TEXT3, bg); tft.setTextSize(2); tft.setCursor(80, 100); tft.print("Waiting for data");
  tft.setTextColor(COL_TEXT, bg); tft.setTextSize(2);
  IPAddress ip = WiFi.localIP();
  if (ip != IPAddress(0, 0, 0, 0)) {
    char buf[40];
    snprintf(buf, sizeof(buf), "IP: %s", ip.toString().c_str());
    tft.setCursor(80, 126); tft.print(buf);
  } else {
    tft.setCursor(80, 126); tft.print("IP: connecting...");
  }
  tft.setTextColor(COL_TEXT3, bg); tft.setTextSize(1); tft.setCursor(80, 156); tft.print("Mac -> CYD");
}

static void renderPage(uint8_t idx, const char* key) {
  if (strcmp(key, "system") == 0) return renderSystem(idx);
  if (strcmp(key, "ccswitch") == 0) return renderApis(idx);
  if (strcmp(key, "clash") == 0) return renderClash(idx);
  if (strcmp(key, "codex") == 0) return renderCodex(idx);
  if (strcmp(key, "weather") == 0) return renderWeather(idx);
  if (strcmp(key, "datetime") == 0) return renderDatetime(idx);
  if (strcmp(key, "omlx") == 0) return renderOmlx(idx);
}

static void processLine(const char* line) {
  JsonDocument tmp;
  DeserializationError err = deserializeJson(tmp, line);
  if (err) {
    Serial.printf("JSON parse error: %s\n", err.c_str());
    return;
  }
  app.doc.clear();
  app.doc.set(tmp);
  JsonObject ctl = app.doc["_control"];
  if (!ctl.isNull() && ctl["pages"].is<JsonArray>()) {
    normalizePages(ctl["pages"].as<JsonArray>());
  }
  app.hasData = true;
  app.lastRender = 0;
}

static void handleStreamData() {
  if (!app.client.connected()) return;
  while (app.client.available()) {
    int ch = app.client.read();
    if (ch < 0) break;
    if (ch == '\n') {
      app.rxLine[app.rxLen] = 0;
      if (app.rxLen > 0 && !app.rxOverrun) processLine(app.rxLine);
      app.rxLen = 0;
      app.rxOverrun = false;
      continue;
    }
    if (app.rxLen < MAX_JSON - 1) {
      app.rxLine[app.rxLen++] = (char)ch;
    } else {
      app.rxOverrun = true;
    }
  }
}

static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    app.wifiReady = true;
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(SIDEMON_DEFAULT_SSID, SIDEMON_DEFAULT_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
  }
  app.wifiReady = WiFi.status() == WL_CONNECTED;
  if (app.wifiReady) {
    Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("WiFi connect timeout");
  }
}

static void connectTcp() {
  if (app.client.connected()) return;
  IPAddress broadcast(255, 255, 255, 255);
  app.udp.beginPacket(broadcast, 9878);
  app.udp.printf("{\"type\":\"sidemon\",\"port\":%u}", app.tcpPort);
  app.udp.endPacket();

  if (!app.client.connect(IPAddress(255, 255, 255, 255), app.tcpPort)) {
    // fallback to broadcast doesn't work for TCP, rely on Mac sender discovering us
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(TFT_BL_PIN, OUTPUT);
  digitalWrite(TFT_BL_PIN, HIGH);

  tft.init();
  tft.setRotation(1);
  tft.fillScreen(COL_BG);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(2);
  tft.setCursor(20, 20);
  tft.print("SideMon CYD booting...");

  resetAppState();
  connectWifi();
  renderWaiting();
}

void loop() {
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    app.wifiReady = false;
    if (now - app.lastReconnect > 5000) {
      app.lastReconnect = now;
      connectWifi();
      renderWaiting();
    }
    return;
  }

  if (!app.client.connected()) {
    if (now - app.lastDiscovery > 3000) {
      app.lastDiscovery = now;
      connectTcp();
    }
    renderWaiting();
    return;
  }

  handleStreamData();

  if (!app.hasData) {
    if (now - app.lastRender > 2000) {
      app.lastRender = now;
      renderWaiting();
    }
    return;
  }

  if (now - app.lastRender >= 15000 || app.lastRender == 0) {
    app.lastRender = now;
    if (app.currentPage >= app.activeCount) app.currentPage = 0;
    renderPage(app.currentPage, app.pageKeys[app.currentPage]);
    app.currentPage = (app.currentPage + 1) % app.activeCount;
  }
}
