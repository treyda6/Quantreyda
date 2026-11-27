// Checks app/brokerimport.js against made-up exports shaped like the real
// ones. All account numbers, names and amounts here are fake.
//   node tests/brokerimport_test.js      (mine.py --selftest runs it too)
const assert = require("assert");
const path = require("path");
const I = require(path.join(__dirname, "..", "app", "brokerimport.js"));

// Fidelity: Positions > Download (Portfolio_Positions_Nov-27-2026.csv)
const fidelity = "﻿Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value," +
  "Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account," +
  "Cost Basis Total,Average Cost Basis,Type\r\n" +
  "Z00011122,ROTH IRA,SPAXX**,HELD IN MONEY MARKET,,,,$412.50,,,,,2.1%,,,Cash,\r\n" +
  "Z00011122,ROTH IRA,VTI,VANGUARD INDEX FDS VANGUARD TOTAL STK MKT ETF,20,$300.10,+$1.20,\"$6,002.00\",+$24.00,+0.40%," +
  "\"+$1,802.00\",+42.90%,30.5%,\"$4,200.00\",$210.00,Cash,\r\n" +
  "Z00011122,ROTH IRA,FZILX,FIDELITY ZERO INTERNATIONAL INDEX,150.512,$14.02,+$0.05,\"$2,110.18\",,,,,,\"$1,800.00\",$11.96,Cash,\r\n" +
  "Z00011122,ROTH IRA,Pending Activity,,,,,$-50.00,,,,,,,,,\r\n" +
  "Z00033344,UTMA - EMMA,QQQ,INVESCO QQQ TR UNIT SER 1,3,$510.00,,\"$1,530.00\",,,,,,$900.00,$300.00,Cash,\r\n" +
  "Z00033344,UTMA - EMMA,912828YK0,UNITED STATES TREAS NTS 1.375% 10/15/2026,1000,$99.10,,$991.00,,,,,,,,Cash,\r\n" +
  "Z00033344,UTMA - EMMA,-AAPL261218C200,AAPL DEC 18 2026 200 CALL,1,$5.00,,$500.00,,,,,,,,Margin,\r\n" +
  "123456789,BrokerageLink,SCHD,SCHWAB US DIVIDEND EQUITY ETF,40,$27.50,,\"$1,100.00\",,,,,,--,--,Cash,\r\n" +
  "\r\n" +
  "\"The data and information in this spreadsheet is provided to you solely for your use and is not for distribution. " +
  "The spreadsheet is provided for informational purposes only.\"\r\n" +
  "\"Date downloaded Nov-27-2026 3:14 p.m ET\"\r\n";

let r = I.parse(fidelity, "Portfolio_Positions_Nov-27-2026.csv");
assert.strictEqual(r.broker, "Fidelity");
assert.strictEqual(r.accounts.length, 3, JSON.stringify(r.accounts.map((a) => a.name)));
const [roth, utma, link] = r.accounts;
assert.deepStrictEqual([roth.name, roth.last4, roth.kind], ["ROTH IRA", "1122", "roth"]);
assert.deepStrictEqual(roth.holdings.map((h) => h.t), ["VTI", "FZILX"]);
assert.strictEqual(roth.holdings[0].sh, 20);
assert.strictEqual(roth.holdings[0].cost, 210);
assert.strictEqual(roth.holdings[1].cost, 11.96);
assert.strictEqual(roth.cash, 362.5, "core money market plus pending activity count as cash");
assert.deepStrictEqual([utma.kind, utma.holdings.length, utma.skipped.length], ["utma", 1, 2], "bond and option listed as skipped");
assert.strictEqual(link.kind, "k401");
assert.strictEqual(link.holdings[0].cost, 0, "-- cost means unknown");
assert(!JSON.stringify(r).includes("00011122"), "full account numbers are never kept");

// Schwab: Positions > Export, all accounts (sections per account)
const schwab = "\"Positions for All-Accounts as of 10:32 AM ET, 2026/11/27\"\n\n" +
  "\"Roth Contributory IRA ...456\"\n" +
  "\"Symbol\",\"Description\",\"Qty (Quantity)\",\"Price\",\"Price Chng % (Price Change %)\",\"Mkt Val (Market Value)\"," +
  "\"Cost Basis\",\"Gain $ (Gain/Loss $)\",\"Reinvest?\",\"Security Type\"\n" +
  "\"SCHD\",\"SCHWAB US DIVIDEND EQUITY ETF\",\"100\",\"$27.50\",\"0.5%\",\"$2,750.00\",\"$2,100.00\",\"$650.00\",\"Yes\",\"ETFs & Closed End Funds\"\n" +
  "\"BRK/B\",\"BERKSHIRE HATHAWAY INC CLASS B\",\"2\",\"$480.00\",\"0.1%\",\"$960.00\",\"$700.00\",\"$260.00\",\"No\",\"Equity\"\n" +
  "\"Cash & Cash Investments\",\"--\",\"--\",\"--\",\"--\",\"$1,234.56\",\"--\",\"--\",\"--\",\"Cash and Money Market\"\n" +
  "\"Account Total\",\"--\",\"--\",\"--\",\"--\",\"$4,944.56\",\"$2,800.00\",\"--\",\"--\",\"--\"\n\n" +
  "\"Individual ...789\"\n" +
  "\"Symbol\",\"Description\",\"Qty (Quantity)\",\"Price\",\"Price Chng % (Price Change %)\",\"Mkt Val (Market Value)\"," +
  "\"Cost Basis\",\"Gain $ (Gain/Loss $)\",\"Reinvest?\",\"Security Type\"\n" +
  "\"AAPL\",\"APPLE INC\",\"10\",\"$230.00\",\"1%\",\"$2,300.00\",\"$1,500.00\",\"$800.00\",\"No\",\"Equity\"\n" +
  "\"Account Total\",\"--\",\"--\",\"--\",\"--\",\"$2,300.00\",\"--\",\"--\",\"--\",\"--\"\n";
r = I.parse(schwab, "All-Accounts-Positions-2026-11-27.csv");
assert.strictEqual(r.broker, "Schwab");
assert.deepStrictEqual(r.accounts.map((a) => [a.name, a.last4, a.kind]),
  [["Roth Contributory IRA", "456", "roth"], ["Individual", "789", "brokerage"]]);
assert.deepStrictEqual(r.accounts[0].holdings.map((h) => [h.t, h.sh, h.cost]), [["SCHD", 100, 21], ["BRK.B", 2, 350]]);
assert.strictEqual(r.accounts[0].cash, 1234.56);

// Schwab, one account (title line only)
r = I.parse("\"Positions for account Custodial ...321 as of 4:01 PM ET, 2026/11/27\"\n\n" +
  "\"Symbol\",\"Description\",\"Quantity\",\"Price\",\"Market Value\",\"Cost Basis\"\n\"VOO\",\"VANGUARD S&P 500 ETF\",\"3\",\"$550\",\"$1,650\",\"$1,200\"\n", "x.csv");
assert.deepStrictEqual([r.accounts[0].name, r.accounts[0].last4, r.accounts[0].kind], ["Custodial", "321", "utma"]);

// Vanguard: holdings section, then a transactions section to ignore
const vanguard = "Account Number,Investment Name,Symbol,Shares,Share Price,Total Value,\n" +
  "55512345,VANGUARD FEDERAL MONEY MARKET INVESTOR CL,VMFXX,812.33,1,812.33,\n" +
  "55512345,VANGUARD TOTAL STOCK MARKET INDEX ADMIRAL CL,VTSAX,30.5,140.2,4276.1,\n" +
  "55512345,VANGUARD TARGET RETIREMENT 2060,VTTSX,12,52.5,630,\n\n\n" +
  "Account Number,Trade Date,Settlement Date,Transaction Type,Transaction Description,Investment Name,Symbol,Shares,Share Price,Principal Amount,\n" +
  "55512345,2026-11-01,2026-11-03,Buy,Buy,VANGUARD TOTAL STOCK MARKET,VTSAX,1,140,-140,\n";
r = I.parse(vanguard, "ofxdownload.csv");
assert.strictEqual(r.broker, "Vanguard");
assert.strictEqual(r.accounts.length, 1);
assert.deepStrictEqual(r.accounts[0].holdings.map((h) => [h.t, h.sh]), [["VTSAX", 30.5], ["VTTSX", 12]], "transactions ignored");
assert.strictEqual(r.accounts[0].cash, 812.33, "settlement fund is cash");
assert.strictEqual(r.accounts[0].kind, "", "no name: the person picks the type");

// Pasted from a spreadsheet (tabs) and a bare list
r = I.parse("Ticker\tShares\tAvg cost\nnvda\t5\t$120.50\nmsft\t2\t\n", "");
assert.deepStrictEqual(r.accounts[0].holdings.map((h) => [h.t, h.sh, h.cost]), [["NVDA", 5, 120.5], ["MSFT", 2, 0]]);
r = I.parse("VTI 25 231.40\nAAPL, 10, 172.15\nhello world\n", "");
assert.strictEqual(r.broker, "List");
assert.deepStrictEqual(r.accounts[0].holdings.map((h) => h.t), ["VTI", "AAPL"]);
r = I.parse("nothing useful here", "");
assert.strictEqual(r.accounts.length, 0);
assert(r.notes.length);

// account-type guesses
const g = I.guessKind;
assert.deepStrictEqual(["ROTH IRA", "Custodial Roth IRA", "UGMA - Ava", "Health Savings Account", "Rollover IRA",
  "401(k) BrokerageLink", "Joint WROS", "Fidelity 529 - UNIQUE"].map(g),
  ["roth", "custodial_roth", "utma", "hsa", "trad", "k401", "brokerage", "college529"]);
console.log("brokerimport test ok");
