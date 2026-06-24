#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <TFT_eSPI.h>
#include <ArduinoJson.h>
#include <WiFiManager.h>
#include <math.h>
#include "cjk_font.h"

#ifndef SIDEMON_DEFAULT_PORT
#define SIDEMON_DEFAULT_PORT 9877
#endif
#ifndef SIDEMON_AP_NAME
#define SIDEMON_AP_NAME "SideMon-CYD"
#endif

static const uint16_t W = 320, H = 240;
static const size_t MAX_JSON = 8192;

TFT_eSPI tft = TFT_eSPI();

// ── Colors ──
static const uint16_t C_BG      = 0x080C;
static const uint16_t C_CARD    = 0x1823;
static const uint16_t C_DIM     = 0x528A;
static const uint16_t C_TEXT    = 0xBDD7;
static const uint16_t C_TEXT2   = 0x8C51;
static const uint16_t C_WHITE   = TFT_WHITE;
static const uint16_t C_BLACK   = TFT_BLACK;
static const uint16_t C_DGRAY   = TFT_DARKGREY;
static const uint16_t C_LGRAY   = TFT_LIGHTGREY;

static const uint16_t AC_SYS    = 0x07E0;
static const uint16_t AC_API    = 0x07FF;
static const uint16_t AC_CLASH  = 0xFD20;
static const uint16_t AC_CODE   = 0xF81F;
static const uint16_t AC_WTHR   = 0x001F;
static const uint16_t AC_TIME   = 0xFFE0;
static const uint16_t AC_OMLX   = 0x07FF;
static const uint16_t HDR_BG    = 0x10A2;

// ── App State ──
struct {
  bool wifiReady = false, hasData = false;
  unsigned long lastRender = 0, lastDiscovery = 0, lastReconnect = 0;
  size_t activeCount = 1, currentPage = 0;
  uint16_t tcpPort = SIDEMON_DEFAULT_PORT;
  char pageKeys[8][12];
  JsonDocument doc;
  WiFiServer srv{SIDEMON_DEFAULT_PORT};
  WiFiClient client;
  WiFiUDP udp;
  char rxLine[MAX_JSON];
  size_t rxLen = 0;
  bool rxOverrun = false;
} app;

// ── Helpers ──
float constrainF(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void safeCopy(char* dst, size_t cap, const char* src) {
  if (!src || !cap) return;
  size_t n = strlen(src);
  if (n >= cap) n = cap - 1;
  memcpy(dst, src, n); dst[n] = 0;
}

static const char* jstr(JsonObject o, const char* key, const char* def) {
  JsonVariant v = o[key];
  if (v.isNull()) return def;
  const char* s = v.as<const char*>();
  return s ? s : def;
}

static uint16_t pctColor(float pct) {
  if (pct < 0.50f) return 0x07E0;
  if (pct < 0.75f) return 0xFFE0;
  if (pct < 0.90f) return 0xFD20;
  return TFT_RED;
}

// ── CJK Drawing ──
static void drawCJKChar(int x, int y, uint16_t cp, uint16_t col, uint16_t bg) {
  int idx = cjkIdx(cp);
  if (idx < 0) return;
  for (int row = 0; row < C_CHAR_H; row++) {
    uint16_t bits = c_font[idx][row];
    for (int px = 0; px < 16; px++) {
      tft.drawPixel(x + px, y + row, (bits & (0x8000 >> px)) ? col : bg);
    }
  }
}

static int drawMixed(int x, int y, const char* str, uint16_t col, uint16_t bg, int font) {
  int cx = x;
  tft.setTextFont(font);
  const uint8_t* p = (const uint8_t*)str;
  while (*p) {
    if (*p < 0x80) {
      char buf[2] = {(char)*p, 0};
      tft.setTextColor(col, bg);
      tft.setCursor(cx, y);
      tft.print(buf);
      cx += tft.textWidth(buf);
      p++;
    } else if ((*p & 0xF0) == 0xE0 && (p[1] & 0xC0) == 0x80 && (p[2] & 0xC0) == 0x80) {
      uint16_t cp = ((uint16_t)(p[0] & 0x0F) << 12) | ((uint16_t)(p[1] & 0x3F) << 6) | (uint16_t)(p[2] & 0x3F);
      drawCJKChar(cx, y, cp, col, bg);
      cx += 16;
      p += 3;
    } else { p++; }
  }
  return cx;
}

static int textW(const char* str, int font) {
  int w = 0;
  const uint8_t* p = (const uint8_t*)str;
  tft.setTextFont(font);
  while (*p) {
    if (*p < 0x80) { char buf[2] = {(char)*p, 0}; w += tft.textWidth(buf); p++; }
    else if ((*p & 0xF0) == 0xE0 && (p[1] & 0xC0) == 0x80 && (p[2] & 0xC0) == 0x80) { w += 16; p += 3; }
    else { p++; }
  }
  return w;
}

static void drawBar(int x, int y, int w_, int h, float pct, uint16_t fg, uint16_t bg) {
  tft.fillRect(x, y, w_, h, bg);
  int fw = (int)(w_ * constrainF(pct, 0.0f, 1.0f));
  if (fw > 0) tft.fillRect(x, y, fw, h, fg);
}

static void drawRing(int cx, int cy, int r, int thick, float pct, uint16_t fg, uint16_t bg) {
  tft.fillCircle(cx, cy, r, bg);
  tft.fillCircle(cx, cy, r - thick, C_BLACK);
  if (pct > 0.001f) {
    int steps = max(1, (int)(360 * pct));
    for (int i = 0; i < steps; i++) {
      float a = (-90 + i * (360.0f * pct / steps)) * PI / 180.0f;
      for (int t = 0; t < thick; t++) {
        int rx = cx + (int)((r - t) * cos(a));
        int ry = cy + (int)((r - t) * sin(a));
        tft.drawPixel(rx, ry, fg);
      }
    }
  }
}

// ── Header ──
static void drawHeader(uint8_t idx, const char* title, uint16_t accent, uint16_t hdrBg) {
  tft.fillScreen(C_BLACK);
  tft.fillRect(0, 0, W, 30, hdrBg);
  tft.fillRect(0, 28, W, 2, accent);
  drawMixed(10, 14, title, accent, hdrBg, 2);
  tft.setTextColor(C_DGRAY, hdrBg);
  tft.setTextFont(1);
  char buf[8];
  snprintf(buf, sizeof(buf), "%u/%u", idx + 1, (unsigned)app.activeCount);
  tft.setCursor(W - 30, 10);
  tft.print(buf);
}

// ── Status Screen ──
static void renderStatus(const char* a, const char* b, const char* c) {
  tft.fillScreen(C_BLACK);
  tft.fillRect(0, 0, W, 30, HDR_BG);
  tft.fillRect(0, 28, W, 2, AC_SYS);
  drawMixed(10, 14, "SideMon CYD", AC_SYS, HDR_BG, 2);
  int tw = textW(a, 2); drawMixed((W-tw)/2, 80, a, C_WHITE, C_BLACK, 2);
  tw = textW(b, 2); drawMixed((W-tw)/2, 108, b, C_LGRAY, C_BLACK, 2);
  if (c && c[0]) { tw = textW(c, 1); drawMixed((W-tw)/2, 130, c, C_DGRAY, C_BLACK, 1); }
}

// ═══════════════════════════════════════════════════════════════
// PAGE 1: SYSTEM — 3 rings + bottom info
// ═══════════════════════════════════════════════════════════════
static void renderSystem(uint8_t idx) {
  JsonObject s = app.doc["system"];
  float cpu = s["cpu"] | 0.0f, mem = s["mem"] | 0.0f, disk = s["disk"] | 0.0f;
  const char* host = jstr(s, "hostname", "");
  const char* uptime = jstr(s, "uptime", "");

  drawHeader(idx, "系统状态", AC_SYS, HDR_BG);

  int cy = 95, rR = 36, rT = 10;
  int xs[3] = {55, 160, 265};
  float vals[3] = {cpu/100.0f, mem/100.0f, disk/100.0f};
  uint16_t acs[3] = {0x07E0, 0xF81F, 0x001F};
  const char* labels[3] = {"CPU", "内存", "磁盘"};
  char v[8];

  for (int i = 0; i < 3; i++) {
    float p = constrainF(vals[i], 0.0f, 1.0f);
    drawRing(xs[i], cy, rR, rT, p, acs[i], 0x2104);
    snprintf(v, sizeof(v), "%.0f%%", vals[i]*100);
    tft.setTextColor(C_WHITE, C_BLACK); tft.setTextFont(2);
    int tw = tft.textWidth(v);
    tft.setCursor(xs[i] - tw/2, cy - 8); tft.print(v);
    tw = textW(labels[i], 2);
    drawMixed(xs[i] - tw/2, cy + rR + 12, labels[i], C_DGRAY, C_BLACK, 2);
  }

  int by = 168, cw = (W-30)/3, ch = 48;
  tft.fillRoundRect(10, by, cw, ch, 4, C_CARD);
  drawMixed(10+8, by+8, "主机", C_DIM, C_CARD, 1);
  tft.setTextColor(C_TEXT, C_CARD); tft.setTextFont(2);
  tft.setCursor(10+8, by+26); tft.print(host[0]?host:"?");

  tft.fillRoundRect(20+cw, by, cw, ch, 4, C_CARD);
  drawMixed(20+cw+8, by+8, "运行", C_DIM, C_CARD, 1);
  tft.setTextColor(C_TEXT, C_CARD); tft.setTextFont(2);
  tft.setCursor(20+cw+8, by+26); tft.print(uptime[0]?uptime:"?");

  tft.fillRoundRect(30+2*cw, by, cw, ch, 4, C_CARD);
  drawMixed(30+2*cw+8, by+8, "负载", C_DIM, C_CARD, 1);
  float l0=s["load"][0]|0.0f,l1=s["load"][1]|0.0f,l2=s["load"][2]|0.0f;
  tft.setTextColor(C_TEXT2, C_CARD); tft.setTextFont(2);
  tft.setCursor(30+2*cw+8, by+26); tft.printf("%.1f %.1f %.1f", l0, l1, l2);
}

// ═══════════════════════════════════════════════════════════════
// PAGE 2: API — DeepSeek + MiMo + token stats
// ═══════════════════════════════════════════════════════════════
static void renderApis(uint8_t idx) {
  JsonObject a = app.doc["ccswitch"];
  const char* ds = jstr(a, "ds_balance", "?");
  float mm_pct = atof(jstr(a, "mm_balance", "0")) / 100.0f;

  drawHeader(idx, "API 用量", AC_API, HDR_BG);

  int y = 38;
  tft.fillRoundRect(10, y, W-20, 50, 4, C_CARD);
  drawMixed(20, y+30, "DeepSeek", AC_API, C_CARD, 2);
  tft.setTextColor(C_WHITE, C_CARD); tft.setTextFont(4);
  char buf[32]; snprintf(buf, sizeof(buf), "%s CNY", ds);
  tft.setCursor(110, y+24); tft.print(buf);

  y = 96;
  tft.fillRoundRect(10, y, W-20, 62, 4, C_CARD);
  drawMixed(20, y+36, "MiMo", AC_CODE, C_CARD, 2);
  mm_pct = constrainF(mm_pct, 0.0f, 1.0f);
  drawRing(250, y+31, 22, 8, mm_pct, pctColor(mm_pct), 0x2104);
  tft.setTextColor(pctColor(mm_pct), C_BLACK); tft.setTextFont(2);
  snprintf(buf, sizeof(buf), "%.0f%%", mm_pct*100);
  int tw = tft.textWidth(buf); tft.setCursor(250-tw/2, y+24); tft.print(buf);

  y = 168; int cw2 = (W-40)/3;
  const char* tl[] = {"总Token","输出","缓存%"};
  const char* tk[] = {"total_tokens","output_tokens","cache_hit_rate"};
  for (int i=0; i<3; i++) {
    int x = 10 + i*(cw2+10);
    tft.fillRoundRect(x, y, cw2, 50, 4, C_CARD);
    drawMixed(x+4, y+4, tl[i], C_DIM, C_CARD, 2);
    if (i<2) {
      long val = a[tk[i]] | 0L;
      char b[16];
      if (val>=1000000) snprintf(b,sizeof(b),"%.1fM",val/1000000.0);
      else if (val>=1000) snprintf(b,sizeof(b),"%.0fK",val/1000.0);
      else snprintf(b,sizeof(b),"%ld",val);
      tft.setTextColor(C_WHITE, C_CARD); tft.setTextFont(2);
      tw = tft.textWidth(b); tft.setCursor(x+(cw2-tw)/2, y+28); tft.print(b);
    } else {
      float hr = a["cache_hit_rate"] | 0.0f;
      char b[16]; snprintf(b,sizeof(b),"%.1f%%",hr);
      tft.setTextColor(AC_WTHR, C_CARD); tft.setTextFont(2);
      tw = tft.textWidth(b); tft.setCursor(x+(cw2-tw)/2, y+28); tft.print(b);
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// PAGE 3: CLASH
// ═══════════════════════════════════════════════════════════════
static void renderClash(uint8_t idx) {
  JsonObject c = app.doc["clash"];
  const char* node = jstr(c, "current_node", "N/A");
  const char* mode = jstr(c, "mode", "rule");
  const char* tu = jstr(c, "traffic_used", "?");
  const char* tt = jstr(c, "traffic_total", "?");
  const char* expire = jstr(c, "expire_date", "");
  int conns = c["active_connections"] | 0;
  float dl = c["download_total"] | 0.0f;
  float ul = c["upload_total"] | 0.0f;

  drawHeader(idx, "Clash 代理", AC_CLASH, HDR_BG);

  int y=34;
  tft.fillRoundRect(10,y,W-20,28,4,C_CARD);
  tft.setTextColor(AC_CLASH,C_CARD); tft.setTextFont(2);
  tft.setCursor(16,y+6); tft.print(node);

  y=68; int hw=(W-30)/2;
  tft.fillRoundRect(10,y,hw,40,4,C_CARD);
  tft.setTextColor(C_DIM,C_CARD); tft.setTextFont(2);
  tft.setCursor(16,y+4); tft.print("Mode");
  tft.setTextColor(C_WHITE,C_CARD);
  tft.setCursor(16,y+22); tft.print(mode);

  tft.fillRoundRect(20+hw,y,hw,40,4,C_CARD);
  tft.setTextColor(C_DIM,C_CARD); tft.setTextFont(2);
  tft.setCursor(26+hw,y+4); tft.print("Conns");
  tft.setTextColor(C_WHITE,C_CARD);
  tft.setCursor(26+hw,y+22); tft.printf("%d",conns);

  y=114;
  tft.fillRoundRect(10,y,W-20,50,4,C_CARD);
  tft.setTextColor(C_DIM,C_CARD); tft.setTextFont(2);
  tft.setCursor(16,y+4); tft.print("Traffic");
  tft.setTextColor(C_WHITE,C_CARD);
  char tbuf[40]; snprintf(tbuf,sizeof(tbuf),"%s / %s",tu,tt);
  tft.setCursor(16,y+22); tft.print(tbuf);
  float used=0,total=1; sscanf(tu,"%f",&used); sscanf(tt,"%f",&total);
  if(total>0) drawBar(16,y+42,W-32,5,used/total,AC_CLASH,0x2104);

  y=172; int c3=(W-40)/3;
  const char* dlabels[]={"Download","Upload","Expiry"};
  for(int i=0;i<3;i++){
    int x=10+i*(c3+10);
    tft.fillRoundRect(x,y,c3,48,4,C_CARD);
    tft.setTextColor(C_DIM,C_CARD); tft.setTextFont(2);
    tft.setCursor(x+4,y+4); tft.print(dlabels[i]);
    tft.setTextColor(C_WHITE,C_CARD);
    if(i==2){
      tft.setCursor(x+4,y+26); tft.print(expire[0]?expire:"N/A");
    } else {
      char b[16]; float vv=(i==0?dl:ul);
      if(vv>1e9)snprintf(b,sizeof(b),"%.1fGB",vv/1e9);
      else snprintf(b,sizeof(b),"%.0fMB",vv/1e6);
      tft.setCursor(x+4,y+26); tft.print(b);
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// PAGE 4: CODEX
// ═══════════════════════════════════════════════════════════════
static void renderCodex(uint8_t idx) {
  JsonObject c = app.doc["codex"];
  float p5 = c["pct_5h"] | 0.0f, p7 = c["pct_7d"] | 0.0f;
  const char* r5 = jstr(c, "reset_5h", "N/A");
  const char* r7 = jstr(c, "reset_7d", "N/A");

  drawHeader(idx, "Codex 用量", AC_CODE, HDR_BG);

  // Rings: ry=86, rR=40 → ring 46-126, inner 56-116
  // Value font6 (48px) at ry-20=66 → 66-114 fits inside inner(56-116)
  // Label at ry+rR+20=146 → CJK 146-162
  int ry=86, rR=40, rT=10;
  int cxs[2]={90,230};
  float ps[2]={p5/100.0f,p7/100.0f};
  const char* rl[2]={"5小时","7天"};
  char v[8];
  for(int i=0;i<2;i++){
    float pp=constrainF(ps[i],0.0f,1.0f);
    drawRing(cxs[i],ry,rR,rT,pp,pctColor(pp),0x2104);
    snprintf(v,sizeof(v),"%.0f%%",pp*100);
    tft.setTextColor(C_WHITE); tft.setTextFont(4);
    int tw=tft.textWidth(v); tft.setCursor(cxs[i]-tw/2,ry-10); tft.print(v);
    tw=textW(rl[i],2);
    drawMixed(cxs[i]-tw/2,ry+rR+20,rl[i],C_LGRAY,C_BLACK,2);
  }

  // Reset cards: by=168, h=44
  // Label at by+4 → 4-20(16px CJK), Value at by+26 → 26-42
  int by=168, hw2=(W-30)/2;
  tft.fillRoundRect(10,by,hw2,44,4,C_CARD);
  drawMixed(16,by+4,"5小时重置",C_DIM,C_CARD,2);
  tft.setTextColor(C_LGRAY,C_CARD); tft.setTextFont(2);
  tft.setCursor(16,by+26); tft.print(r5);

  tft.fillRoundRect(20+hw2,by,hw2,44,4,C_CARD);
  drawMixed(26+hw2,by+4,"7天重置",C_DIM,C_CARD,2);
  tft.setTextColor(AC_WTHR,C_CARD); tft.setTextFont(2);
  tft.setCursor(26+hw2,by+26); tft.print(r7);
}

// ═══════════════════════════════════════════════════════════════
// PAGE 5: WEATHER
// ═══════════════════════════════════════════════════════════════
// ── Weather translation ──
static const char* transCond(const char* en) {
  if (!en || !en[0]) return "?";
  if (strstr(en, "Sunny") || strstr(en, "Clear")) return "晴朗";
  if (strstr(en, "Partly cloudy")) return "多云";
  if (strstr(en, "Cloudy") || strstr(en, "Overcast")) return "阴天";
  if (strstr(en, "Mist") || strstr(en, "Fog")) return "雾";
  if (strstr(en, "rain") || strstr(en, "drizzle") || strstr(en, "shower")) return "雨";
  if (strstr(en, "thunder")) return "雷雨";
  if (strstr(en, "snow") || strstr(en, "sleet") || strstr(en, "ice")) return "雪";
  if (strstr(en, "wind")) return "大风";
  return en;
}

static void renderWeather(uint8_t idx) {
  JsonObject w = app.doc["weather"];
  const char* city = jstr(w, "city", "?");
  int temp = w["temp_c"] | 0, feels = w["feels_like_c"] | 0;
  int humid = w["humidity"] | 0, uv = w["uv_index"] | 0;
  const char* cond = transCond(jstr(w, "condition", ""));
  float wkph = w["wind_kph"] | 0.0f;
  const char* sunrise = jstr(w, "sunrise", "");
  const char* sunset = jstr(w, "sunset", "");

  drawHeader(idx, "天气", AC_WTHR, HDR_BG);

  // ── Top: city + condition left, big temp right ──
  drawMixed(10, 44, city, C_WHITE, C_BLACK, 2);
  drawMixed(10, 64, cond, AC_WTHR, C_BLACK, 1);

  char ts[8]; snprintf(ts,sizeof(ts),"%d",temp);
  uint16_t tCol;
  if(temp>=30) tCol=TFT_RED;
  else if(temp<10) tCol=TFT_BLUE;
  else tCol=C_WHITE;
  tft.setTextColor(tCol,C_BLACK); tft.setTextFont(7);
  int tw=tft.textWidth(ts);
  tft.setCursor(240-tw,36); tft.print(ts);
  tft.setTextColor(tCol,C_BLACK); tft.setTextFont(2);
  tft.setCursor(248,62); tft.print("C");

  // Divider
  tft.fillRect(10,84,W-20,1,C_DIM);

  // ── 2x2 grid ──
  int gY=94, cellW=135, cellH=30, gap=4;
  char v0[12];snprintf(v0,sizeof(v0),"%dC",feels);
  char v1[12];snprintf(v1,sizeof(v1),"%d%%",humid);
  char v2[20];snprintf(v2,sizeof(v2),"%dkm/h",(int)wkph);
  char v3[8];snprintf(v3,sizeof(v3),"%d",uv);
  const char* gl[]={"体感","湿度","风速","UV"};
  const char* gv[]={v0,v1,v2,v3};

  for(int i=0;i<4;i++){
    int gx=10+(i%2)*(cellW+10), gy=gY+(i/2)*(cellH+gap);
    tft.fillRoundRect(gx,gy,cellW,cellH,4,C_CARD);
    drawMixed(gx+6,gy+2,gl[i],C_DIM,C_CARD,2);
    if(i==3){
      uint16_t uc=uv>=11?TFT_RED:(uv>=8?TFT_ORANGE:(uv>=6?TFT_YELLOW:TFT_GREEN));
      tft.setTextColor(uc,C_CARD);
    } else tft.setTextColor(C_WHITE,C_CARD);
    tft.setTextFont(2); int vw=tft.textWidth(gv[i]);
    tft.setCursor(gx+cellW-vw-6,gy+cellH-16); tft.print(gv[i]);
  }

  // ── Sunrise / Sunset ──
  int sY=gY+2*(cellH+gap)+4; // 94+68+4=166
  tft.fillRect(10,sY,W-20,1,C_DIM);
  sY+=6;
  int sH=40;
  tft.fillRoundRect(10,sY,cellW,sH,4,C_CARD);
  drawMixed(16,sY+2,"日出",AC_WTHR,C_CARD,1);
  tft.setTextColor(C_WHITE,C_CARD); tft.setTextFont(2);
  tft.setCursor(48,sY+22); tft.print(sunrise[0]?sunrise:"N/A");

  tft.fillRoundRect(20+cellW,sY,cellW,sH,4,C_CARD);
  drawMixed(26+cellW,sY+2,"日落",AC_WTHR,C_CARD,1);
  tft.setTextColor(C_WHITE,C_CARD); tft.setCursor(58+cellW,sY+22); tft.print(sunset[0]?sunset:"N/A");
}

// ═══════════════════════════════════════════════════════════════
// PAGE 6: DATETIME
// ═══════════════════════════════════════════════════════════════
static void renderDatetime(uint8_t idx) {
  drawHeader(idx, "日期时间", AC_TIME, HDR_BG);
  time_t ts = app.doc["datetime"]["timestamp"] | (uint32_t)0;
  struct tm tmv; localtime_r(&ts,&tmv);

  char db[16]; strftime(db,sizeof(db),"%Y-%m-%d",&tmv);
  tft.setTextColor(AC_TIME,C_BLACK); tft.setTextFont(4);
  tft.setCursor(10,48); tft.print(db);

  const char* wkdays[]={"星期日","星期一","星期二","星期三","星期四","星期五","星期六"};
  drawMixed(10,78,wkdays[tmv.tm_wday],C_LGRAY,C_BLACK,2);

  char tb[16]; strftime(tb,sizeof(tb),"%H:%M",&tmv);
  tft.setTextColor(C_WHITE,C_BLACK); tft.setTextFont(7);
  int tw=tft.textWidth(tb); tft.setCursor(W-tw-10,42); tft.print(tb);

  // Calendar
  int calY=110, colW=(W-20)/7;
  const char* hdrs[]={"日","一","二","三","四","五","六"};
  for(int d=0;d<7;d++){
    int cx=10+d*colW+colW/2;
    int hw2=textW(hdrs[d],2);
    drawMixed(cx-hw2/2,calY+16,hdrs[d],d==0?TFT_RED:C_DIM,C_BLACK,2);
  }

  struct tm first=tmv; first.tm_mday=1; mktime(&first);
  int startCol=first.tm_wday;
  struct tm nm=first; nm.tm_mon+=1; nm.tm_mday=0; mktime(&nm);
  int dim=nm.tm_mday, today=tmv.tm_mday;
  int row=0,col=startCol;
  for(int d=1;d<=dim;d++){
    int cx=10+col*colW+colW/2, ny=calY+36+row*18;
    char dn[4];snprintf(dn,sizeof(dn),"%d",d);
    tft.setTextFont(1); int dw=tft.textWidth(dn);
    if(d==today){
      tft.fillRoundRect(cx-dw/2-3,ny-1,dw+6,14,3,AC_TIME);
      tft.setTextColor(C_BLACK,AC_TIME);
    } else {
      tft.setTextColor(col==0?TFT_RED:C_LGRAY,C_BLACK);
    }
    tft.setCursor(cx-dw/2,ny); tft.print(dn);
    col++; if(col>=7){col=0;row++;}
  }
}

// ═══════════════════════════════════════════════════════════════
// PAGE 7: OMLX
// ═══════════════════════════════════════════════════════════════
static void renderOmlx(uint8_t idx) {
  JsonObject o = app.doc["omlx"];
  bool running = o["running"] | false;
  int queued=o["queued"]|0, active=o["active_jobs"]|0, done=o["completed"]|0;

  drawHeader(idx, "oMLX 仪表盘", AC_OMLX, HDR_BG);

  int y=44;
  tft.fillCircle(24,y+14,14,running?TFT_GREEN:TFT_RED);
  drawMixed(50,y+30,running?"在线":"离线",C_WHITE,C_BLACK,4);

  y=90; int items[3]={active,queued,done};
  const char* il[]={"活跃","排队","完成"};
  char val[16];
  for(int i=0;i<3;i++){
    int jy=y+i*44;
    tft.fillRoundRect(10,jy,W-20,38,4,C_CARD);
    drawMixed(20,jy+28,il[i],C_DIM,C_CARD,2);
    tft.setTextColor(C_WHITE,C_CARD); tft.setTextFont(4);
    snprintf(val,sizeof(val),"%d",items[i]);
    int tw=tft.textWidth(val); tft.setCursor(W-18-tw,jy+6); tft.print(val);
  }
}

// ═══════════════════════════════════════════════════════════════
// Dispatcher
// ═══════════════════════════════════════════════════════════════
typedef void (*PageFn)(uint8_t);
static const PageFn RENDERERS[] = {
  renderSystem, renderApis, renderClash, renderCodex,
  renderWeather, renderDatetime, renderOmlx
};
static const char* RENDERER_KEYS[] = {
  "system","ccswitch","clash","codex","weather","datetime","omlx"
};
static const size_t RENDERER_COUNT = sizeof(RENDERERS)/sizeof(RENDERERS[0]);

static void renderPage(uint8_t idx, const char* key) {
  for(size_t i=0;i<RENDERER_COUNT;++i)
    if(strcmp(key,RENDERER_KEYS[i])==0){RENDERERS[i](idx);return;}
}

static void resetAppState() {
  app.hasData=false; app.activeCount=1; app.currentPage=0;
  app.rxLen=0; app.rxOverrun=false;
  safeCopy(app.pageKeys[0],sizeof(app.pageKeys[0]),"system");
  for(size_t i=1;i<8;++i)app.pageKeys[i][0]=0;
}

static void normalizePages(JsonArray pages) {
  static const char* known[]={"system","ccswitch","clash","codex","weather","datetime","omlx"};
  size_t idx=0;
  if(!pages.isNull()){
    for(JsonVariant v:pages){
      const char* key=v.as<const char*>(); if(!key)continue;
      bool valid=false;
      for(auto k:known){if(strcmp(k,key)==0){valid=true;break;}}
      if(!valid)continue;
      bool dup=false;
      for(size_t j=0;j<idx;++j){if(strcmp(app.pageKeys[j],key)==0){dup=true;break;}}
      if(!dup&&idx<8){safeCopy(app.pageKeys[idx],sizeof(app.pageKeys[idx]),key);++idx;}
    }
  }
  if(idx==0){for(auto k:known){safeCopy(app.pageKeys[idx],sizeof(app.pageKeys[idx]),k);if(++idx>=7)break;}}
  app.activeCount=idx;
  if(app.currentPage>=app.activeCount)app.currentPage=0;
}

static void processLine(const char* line) {
  JsonDocument tmp;
  DeserializationError err=deserializeJson(tmp,line);
  if(err){Serial.printf("JSON_ERR:%s\n",err.c_str());return;}
  bool firstData = !app.hasData;
  app.doc.clear(); app.doc.set(tmp);
  JsonObject ctl=app.doc["_control"];
  if(!ctl.isNull()&&ctl["pages"].is<JsonArray>())normalizePages(ctl["pages"].as<JsonArray>());
  app.hasData=true;
  if(firstData) app.lastRender=0;
}

static void handleStreamData() {
  if(!app.client.connected())return;
  while(app.client.available()){
    int ch=app.client.read(); if(ch<0)break;
    if(ch=='\n'){app.rxLine[app.rxLen]=0;
      if(app.rxLen>0&&!app.rxOverrun)processLine(app.rxLine);
      app.rxLen=0;app.rxOverrun=false;continue;}
    if(app.rxLen<MAX_JSON-1)app.rxLine[app.rxLen++]=(char)ch;else app.rxOverrun=true;
  }
}

// ── Network ──
static void broadcastDiscovery() {
  IPAddress bcast(255,255,255,255);
  app.udp.beginPacket(bcast,9878);
  app.udp.printf("{\"type\":\"sidemon\",\"port\":%u}",app.tcpPort);
  app.udp.endPacket();
}

static void startListening(){app.srv.begin();app.srv.setNoDelay(true);}

static bool connectWifi(){
  if(WiFi.status()==WL_CONNECTED){app.wifiReady=true;return true;}
  renderStatus("WiFi连接中...","请稍候","");
  WiFi.mode(WIFI_STA);WiFi.setSleep(false);WiFi.begin();
  unsigned long s=millis();
  while(WiFi.status()!=WL_CONNECTED&&millis()-s<12000){delay(500);}
  if(WiFi.status()==WL_CONNECTED){
    app.wifiReady=true;
    tft.fillScreen(C_BLACK);
    drawMixed((W-16*5)/2,60,"WiFi已连接!",AC_SYS,C_BLACK,4);
    tft.setTextColor(C_LGRAY,C_BLACK);tft.setTextFont(2);
    char info[48];snprintf(info,sizeof(info),"IP %s TCP %d",WiFi.localIP().toString().c_str(),app.tcpPort);
    int tw=tft.textWidth(info);tft.setCursor((W-tw)/2,100);tft.print(info);
    delay(2000);return true;
  }
  WiFi.disconnect(true);delay(200);
  tft.fillScreen(0x2000);
  drawMixed((W-16*6)/2,40,"WiFi配网模式",C_WHITE,0x2000,2);
  tft.setTextColor(C_LGRAY,0x2000);tft.setTextFont(1);
  tft.setCursor(10,70);tft.print("1. 连接WiFi:");
  tft.setCursor(10,86);tft.print("2. 打开 192.168.4.1");
  tft.setCursor(10,102);tft.print("3. 选择网络并配置");
  WiFiManager wm;wm.setConfigPortalTimeout(0);
  bool ok=wm.startConfigPortal(SIDEMON_AP_NAME);
  app.wifiReady=ok&&WiFi.status()==WL_CONNECTED;
  if(app.wifiReady){tft.fillScreen(C_BLACK);drawMixed((W-16*4)/2,80,"已配置!",AC_SYS,C_BLACK,4);delay(2000);}
  return app.wifiReady;
}

void setup(){
  Serial.begin(115200);delay(200);
  setenv("TZ","CST-8",1);tzset();
  pinMode(TFT_BL,OUTPUT);digitalWrite(TFT_BL,HIGH);delay(100);
  tft.init();tft.invertDisplay(true);delay(50);
  ledcSetup(0,5000,8);ledcAttachPin(TFT_BL,0);ledcWrite(0,200);
  tft.setRotation(3);tft.fillScreen(C_BLACK);
  resetAppState();
  drawMixed((W-16*8)/2,70,"SideMon CYD",AC_SYS,C_BLACK,4);
  drawMixed((W-16*4)/2,110,"启动中...",C_LGRAY,C_BLACK,2);
  connectWifi();
  if(app.wifiReady){startListening();
    char info[48];snprintf(info,sizeof(info),"IP %s TCP %d",WiFi.localIP().toString().c_str(),app.tcpPort);
    renderStatus("等待数据","TCP就绪",info);
  }
}

void loop(){
  unsigned long now=millis();
  if(WiFi.status()!=WL_CONNECTED){
    app.wifiReady=false;
    if(now-app.lastReconnect>8000){app.lastReconnect=now;connectWifi();if(app.wifiReady)startListening();}
    return;
  }
  if(!app.client.connected()){
    if(now-app.lastDiscovery>5000){app.lastDiscovery=now;broadcastDiscovery();}
    WiFiClient inc=app.srv.available();
    if(inc){
      app.client=inc;
      if(!app.hasData) renderStatus("已连接","接收中...",app.client.remoteIP().toString().c_str());
    }
    return;
  }
  handleStreamData();
  if(!app.hasData){
    if(now-app.lastRender>2000){app.lastRender=now;renderStatus("等待数据","客户端已连接","");}
    return;
  }
  unsigned long pageInterval = (app.doc["_control"]["page_interval"] | 15UL) * 1000UL;
  if(now-app.lastRender>=pageInterval||app.lastRender==0){
    app.lastRender=now;
    if(app.currentPage>=app.activeCount)app.currentPage=0;
    renderPage(app.currentPage,app.pageKeys[app.currentPage]);
    app.currentPage=(app.currentPage+1)%app.activeCount;
  }
}
