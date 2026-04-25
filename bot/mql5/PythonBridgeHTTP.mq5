//+------------------------------------------------------------------+
//| PythonBridgeHTTP.mq5                                             |
//| HTTP bridge — no shared folders required.                        |
//| Connects to Python FastAPI server on macOS host (192.168.64.1)  |
//+------------------------------------------------------------------+
#property version "1.00"

input string SERVER_URL      = "http://192.168.64.1:8080"; // macOS bridge server
input int    TICK_INTERVAL   = 1000;  // ms between tick pushes
input int    CMD_POLL_MS     = 500;   // ms between command polls
input bool   LOG_VERBOSE     = false;

string url_tick, url_account, url_heartbeat, url_command, url_result;

//+------------------------------------------------------------------+
int OnInit()
{
   url_tick      = SERVER_URL + "/tick";
   url_account   = SERVER_URL + "/account";
   url_heartbeat = SERVER_URL + "/heartbeat";
   url_command   = SERVER_URL + "/command";
   url_result    = SERVER_URL + "/result";

   // Verify connection
   string resp = HttpPost(SERVER_URL + "/heartbeat", "{}");
   if (resp == "")
   {
      Alert("PythonBridgeHTTP: Cannot reach server at " + SERVER_URL +
            ". Check Python server is running and WebRequest is allowed.");
      return INIT_FAILED;
   }

   EventSetMillisecondTimer(MathMin(TICK_INTERVAL, CMD_POLL_MS));
   Print("PythonBridgeHTTP connected to ", SERVER_URL);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
void OnTick()  { PushTickData(); }

void OnTimer()
{
   static int tick_counter = 0;
   static int cmd_counter  = 0;

   tick_counter += 100;
   cmd_counter  += 100;

   if (tick_counter >= TICK_INTERVAL)   { PushTickData();    tick_counter = 0; }
   if (tick_counter >= 5000)            { PushAccountData(); }
   if (cmd_counter  >= CMD_POLL_MS)     { PollCommand();     cmd_counter  = 0; }
}

//+------------------------------------------------------------------+
void PushTickData()
{
   string sym = Symbol();
   MqlTick tick;
   if (!SymbolInfoTick(sym, tick)) return;

   MqlRates rates[];
   CopyRates(sym, PERIOD_H1, 0, 1, rates);

   string body = "{";
   body += "\"symbol\":\"" + sym + "\",";
   body += "\"bid\":"    + DoubleToString(tick.bid, 5) + ",";
   body += "\"ask\":"    + DoubleToString(tick.ask, 5) + ",";
   body += "\"spread\":" + DoubleToString((tick.ask - tick.bid) /
                           SymbolInfoDouble(sym, SYMBOL_POINT), 1) + ",";
   body += "\"time\":"   + IntegerToString(tick.time) + ",";
   body += "\"volume\":" + IntegerToString(tick.volume);
   if (ArraySize(rates) > 0)
   {
      body += ",\"h1_open\":"  + DoubleToString(rates[0].open, 5);
      body += ",\"h1_high\":"  + DoubleToString(rates[0].high, 5);
      body += ",\"h1_low\":"   + DoubleToString(rates[0].low, 5);
      body += ",\"h1_close\":" + DoubleToString(rates[0].close, 5);
   }
   body += "}";

   HttpPost(url_tick, body);
}

//+------------------------------------------------------------------+
void PushAccountData()
{
   string body = "{";
   body += "\"balance\":"     + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   body += "\"equity\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   body += "\"margin\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",";
   body += "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_FREEMARGIN), 2) + ",";
   body += "\"profit\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT), 2) + ",";
   body += "\"leverage\":"    + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",";
   body += "\"currency\":\""  + AccountInfoString(ACCOUNT_CURRENCY) + "\",";
   body += "\"server\":\""    + AccountInfoString(ACCOUNT_SERVER) + "\"";
   body += "}";
   HttpPost(url_account, body);
}

//+------------------------------------------------------------------+
void PollCommand()
{
   string resp = HttpGet(url_command);
   if (resp == "" || resp == "{\"action\":\"NONE\"}") return;
   if (LOG_VERBOSE) Print("Command: ", resp);

   string action = ExtractField(resp, "action");

   if (action == "PING")
      HttpPost(url_result, "{\"action\":\"PING\",\"success\":true,\"result\":\"PONG\"}");
   else if (action == "BUY" || action == "SELL")
      ExecuteTrade(action, resp);
   else if (action == "CLOSE")
      CloseTrade((ulong)StringToInteger(ExtractField(resp, "ticket")));
}

//+------------------------------------------------------------------+
void ExecuteTrade(string action, string json)
{
   string sym    = ExtractField(json, "symbol");
   double volume = StringToDouble(ExtractField(json, "volume"));
   double sl     = StringToDouble(ExtractField(json, "sl"));
   double tp     = StringToDouble(ExtractField(json, "tp"));
   if (sym    == "") sym    = Symbol();
   if (volume == 0)  volume = 0.01;

   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = sym;
   req.volume    = volume;
   req.type      = (action == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price     = (action == "BUY") ? SymbolInfoDouble(sym, SYMBOL_ASK)
                                      : SymbolInfoDouble(sym, SYMBOL_BID);
   req.deviation = 20;
   req.magic     = 12345;
   if (sl > 0) req.sl = sl;
   if (tp > 0) req.tp = tp;

   bool ok = OrderSend(req, res);
   string result = "{\"action\":\"" + action + "\",";
   result += "\"success\":"  + (ok ? "true" : "false") + ",";
   result += "\"ticket\":"   + IntegerToString(res.deal) + ",";
   result += "\"retcode\":"  + IntegerToString(res.retcode) + "}";
   HttpPost(url_result, result);
}

//+------------------------------------------------------------------+
void CloseTrade(ulong ticket)
{
   if (!PositionSelectByTicket(ticket)) return;
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action   = TRADE_ACTION_DEAL;
   req.position = ticket;
   req.symbol   = PositionGetString(POSITION_SYMBOL);
   req.volume   = PositionGetDouble(POSITION_VOLUME);
   req.type     = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                  ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price    = (req.type == ORDER_TYPE_SELL)
                  ? SymbolInfoDouble(req.symbol, SYMBOL_BID)
                  : SymbolInfoDouble(req.symbol, SYMBOL_ASK);
   req.deviation = 20;
   bool ok = OrderSend(req, res);
   HttpPost(url_result, "{\"action\":\"CLOSE\",\"success\":" +
            (ok ? "true" : "false") + ",\"ticket\":" + IntegerToString(ticket) + "}");
}

//+------------------------------------------------------------------+
// HTTP helpers
//+------------------------------------------------------------------+
string HttpPost(string url, string body)
{
   char data[], result[];
   string headers = "Content-Type: application/json\r\n";
   StringToCharArray(body, data, 0, StringLen(body));
   string resp_headers;
   int code = WebRequest("POST", url, headers, 5000, data, result, resp_headers);
   if (code == -1 && LOG_VERBOSE) Print("WebRequest error: ", GetLastError(), " url=", url);
   return (code > 0) ? CharArrayToString(result) : "";
}

string HttpGet(string url)
{
   char data[], result[];
   string headers, resp_headers;
   int code = WebRequest("GET", url, headers, 3000, data, result, resp_headers);
   return (code > 0) ? CharArrayToString(result) : "";
}

string ExtractField(string json, string field)
{
   string search = "\"" + field + "\":";
   int start = StringFind(json, search);
   if (start < 0) return "";
   start += StringLen(search);
   bool is_str = (StringSubstr(json, start, 1) == "\"");
   if (is_str) start++;
   int end = is_str ? StringFind(json, "\"", start)
                    : MathMin(StringFind(json, ",", start),
                              StringFind(json, "}", start));
   if (end < 0) end = StringLen(json);
   return StringSubstr(json, start, end - start);
}
//+------------------------------------------------------------------+
