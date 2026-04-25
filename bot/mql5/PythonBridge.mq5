//+------------------------------------------------------------------+
//| PythonBridge.mq5                                                  |
//| File-based JSON IPC bridge between MT5 and macOS Python bot      |
//| Write path: configured via SHARED_FOLDER input parameter         |
//+------------------------------------------------------------------+
#property copyright "Trading Bot"
#property version   "1.00"
#property strict

input string SHARED_FOLDER   = "C:\\mt5bridge\\";  // Shared folder path
input int    POLL_INTERVAL_MS = 500;                // Command poll interval (ms)
input bool   LOG_VERBOSE      = false;              // Verbose logging

string PRICE_FILE;
string ACCOUNT_FILE;
string COMMANDS_FILE;
string RESULTS_FILE;
string HEARTBEAT_FILE;

//+------------------------------------------------------------------+
int OnInit()
{
   PRICE_FILE     = SHARED_FOLDER + "price.json";
   ACCOUNT_FILE   = SHARED_FOLDER + "account.json";
   COMMANDS_FILE  = SHARED_FOLDER + "commands.json";
   RESULTS_FILE   = SHARED_FOLDER + "trade_results.txt";
   HEARTBEAT_FILE = SHARED_FOLDER + "heartbeat.json";

   if (!FolderCreate(SHARED_FOLDER, FILE_COMMON))
      if (GetLastError() != 5018) // ignore "already exists"
         Print("Warning: Could not create shared folder: ", SHARED_FOLDER);

   EventSetMillisecondTimer(POLL_INTERVAL_MS);
   WritePriceData();
   WriteAccountData();
   WriteHeartbeat();
   Print("PythonBridge EA started. Shared folder: ", SHARED_FOLDER);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("PythonBridge EA stopped.");
}

//+------------------------------------------------------------------+
void OnTick()
{
   WritePriceData();
}

//+------------------------------------------------------------------+
void OnTimer()
{
   WriteHeartbeat();
   WriteAccountData();
   ProcessCommands();
}

//+------------------------------------------------------------------+
void WritePriceData()
{
   string sym = Symbol();
   MqlTick tick;
   if (!SymbolInfoTick(sym, tick))
      return;

   MqlRates rates[];
   int copied = CopyRates(sym, PERIOD_H1, 0, 2, rates);

   string json = "{";
   json += "\"symbol\":\"" + sym + "\",";
   json += "\"bid\":" + DoubleToString(tick.bid, 5) + ",";
   json += "\"ask\":" + DoubleToString(tick.ask, 5) + ",";
   json += "\"spread\":" + DoubleToString((tick.ask - tick.bid) / SymbolInfoDouble(sym, SYMBOL_POINT), 1) + ",";
   json += "\"time\":" + IntegerToString(tick.time) + ",";
   json += "\"volume\":" + IntegerToString(tick.volume);

   if (copied >= 2)
   {
      json += ",\"h1_open\":"    + DoubleToString(rates[0].open, 5);
      json += ",\"h1_high\":"    + DoubleToString(rates[0].high, 5);
      json += ",\"h1_low\":"     + DoubleToString(rates[0].low, 5);
      json += ",\"h1_close\":"   + DoubleToString(rates[0].close, 5);
      json += ",\"h1_volume\":"  + IntegerToString(rates[0].tick_volume);
   }
   json += "}";

   WriteFile(PRICE_FILE, json);
}

//+------------------------------------------------------------------+
void WriteAccountData()
{
   string json = "{";
   json += "\"balance\":"     + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   json += "\"margin\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",";
   json += "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_FREEMARGIN), 2) + ",";
   json += "\"profit\":"      + DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT), 2) + ",";
   json += "\"leverage\":"    + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",";
   json += "\"currency\":\"" + AccountInfoString(ACCOUNT_CURRENCY) + "\",";
   json += "\"server\":\""   + AccountInfoString(ACCOUNT_SERVER) + "\"";
   json += "}";

   WriteFile(ACCOUNT_FILE, json);
}

//+------------------------------------------------------------------+
void WriteHeartbeat()
{
   string json = "{\"alive\":true,\"time\":" + IntegerToString(TimeCurrent()) + "}";
   WriteFile(HEARTBEAT_FILE, json);
}

//+------------------------------------------------------------------+
void ProcessCommands()
{
   if (!FileIsExist(COMMANDS_FILE, FILE_COMMON))
      return;

   int fh = FileOpen(COMMANDS_FILE, FILE_READ | FILE_TXT | FILE_COMMON);
   if (fh == INVALID_HANDLE)
      return;

   string content = "";
   while (!FileIsEnding(fh))
      content += FileReadString(fh);
   FileClose(fh);

   if (content == "" || content == "{}" || content == "null")
      return;

   // Clear command file immediately to prevent re-processing
   FileDelete(COMMANDS_FILE, FILE_COMMON);

   if (LOG_VERBOSE) Print("Command received: ", content);

   string action = ExtractField(content, "action");

   if (action == "PING")
   {
      AppendResult("{\"action\":\"PING\",\"result\":\"PONG\",\"time\":" + IntegerToString(TimeCurrent()) + "}");
   }
   else if (action == "BUY" || action == "SELL")
   {
      ExecuteTrade(action, content);
   }
   else if (action == "CLOSE")
   {
      string ticket_str = ExtractField(content, "ticket");
      ulong ticket = (ulong)StringToInteger(ticket_str);
      CloseTrade(ticket);
   }
   else if (action == "GET_POSITIONS")
   {
      WritePositions();
   }
}

//+------------------------------------------------------------------+
void ExecuteTrade(string action, string json)
{
   string sym    = ExtractField(json, "symbol");
   double volume = StringToDouble(ExtractField(json, "volume"));
   double sl     = StringToDouble(ExtractField(json, "sl"));
   double tp     = StringToDouble(ExtractField(json, "tp"));

   if (sym == "") sym = Symbol();
   if (volume <= 0) volume = 0.01;

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
   req.comment   = "PythonBridge";
   if (sl > 0) req.sl = sl;
   if (tp > 0) req.tp = tp;

   bool ok = OrderSend(req, res);
   string result = "{\"action\":\"" + action + "\",";
   result += "\"success\":" + (ok ? "true" : "false") + ",";
   result += "\"ticket\":" + IntegerToString(res.deal) + ",";
   result += "\"retcode\":" + IntegerToString(res.retcode) + ",";
   result += "\"comment\":\"" + res.comment + "\"}";

   AppendResult(result);
}

//+------------------------------------------------------------------+
void CloseTrade(ulong ticket)
{
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};

   if (!PositionSelectByTicket(ticket)) {
      AppendResult("{\"action\":\"CLOSE\",\"success\":false,\"ticket\":" + IntegerToString(ticket) + ",\"error\":\"position not found\"}");
      return;
   }

   req.action   = TRADE_ACTION_DEAL;
   req.position = ticket;
   req.symbol   = PositionGetString(POSITION_SYMBOL);
   req.volume   = PositionGetDouble(POSITION_VOLUME);
   req.type     = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price    = (req.type == ORDER_TYPE_SELL) ? SymbolInfoDouble(req.symbol, SYMBOL_BID)
                                                 : SymbolInfoDouble(req.symbol, SYMBOL_ASK);
   req.deviation = 20;

   bool ok = OrderSend(req, res);
   AppendResult("{\"action\":\"CLOSE\",\"success\":" + (ok ? "true" : "false") + ",\"ticket\":" + IntegerToString(ticket) + "}");
}

//+------------------------------------------------------------------+
void WritePositions()
{
   string json = "[";
   int total = PositionsTotal();
   for (int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if (PositionSelectByTicket(ticket))
      {
         if (i > 0) json += ",";
         json += "{";
         json += "\"ticket\":"  + IntegerToString(ticket) + ",";
         json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
         json += "\"type\":"    + IntegerToString(PositionGetInteger(POSITION_TYPE)) + ",";
         json += "\"volume\":"  + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
         json += "\"profit\":"  + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ",";
         json += "\"sl\":"      + DoubleToString(PositionGetDouble(POSITION_SL), 5) + ",";
         json += "\"tp\":"      + DoubleToString(PositionGetDouble(POSITION_TP), 5);
         json += "}";
      }
   }
   json += "]";
   WriteFile(SHARED_FOLDER + "positions.json", json);
}

//+------------------------------------------------------------------+
// Helpers
//+------------------------------------------------------------------+
void WriteFile(string path, string content)
{
   int fh = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_COMMON);
   if (fh == INVALID_HANDLE) return;
   FileWriteString(fh, content);
   FileClose(fh);
}

void AppendResult(string line)
{
   int fh = FileOpen(RESULTS_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_COMMON);
   if (fh != INVALID_HANDLE)
   {
      FileSeek(fh, 0, SEEK_END);
      FileWriteString(fh, line + "\n");
      FileClose(fh);
   }
}

string ExtractField(string json, string field)
{
   string search = "\"" + field + "\":";
   int start = StringFind(json, search);
   if (start < 0) return "";
   start += StringLen(search);

   bool is_string = (StringSubstr(json, start, 1) == "\"");
   if (is_string) start++;

   int end = is_string ? StringFind(json, "\"", start)
                        : MathMin(StringFind(json, ",", start),
                                  StringFind(json, "}", start));
   if (end < 0) end = StringLen(json);
   return StringSubstr(json, start, end - start);
}
//+------------------------------------------------------------------+
