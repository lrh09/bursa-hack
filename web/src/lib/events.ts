// Key Bursa Malaysia / global macro events overlaid on the equity curve.
// Each is annotated with the period the strategy was navigating.

export interface MacroEvent {
  date: string;
  label: string;
  blurb: string;
  category: "crisis" | "policy" | "structural";
}

export const KEY_EVENTS: MacroEvent[] = [
  {
    date: "2008-09-15",
    label: "Lehman / GFC",
    blurb: "Global financial crisis. KLCI fell 39% between Jan-Oct 2008. Bursa hits cycle low Oct 28.",
    category: "crisis",
  },
  {
    date: "2011-08-05",
    label: "US debt ceiling / S&P downgrade",
    blurb: "US sovereign downgrade triggers global risk-off; KLCI -16% July-Sep.",
    category: "crisis",
  },
  {
    date: "2014-06-19",
    label: "Brent oil collapse",
    blurb: "Brent crude breaks below 100/bbl, eventually halves by Jan 2015. Hit Bursa's energy + plantation weights.",
    category: "structural",
  },
  {
    date: "2015-08-11",
    label: "Ringgit shock",
    blurb: "PBoC devalues yuan; ringgit hits 17-year low. 1MDB scandal escalates. Combined macro + political stress.",
    category: "crisis",
  },
  {
    date: "2016-11-04",
    label: "Capital controls debate",
    blurb: "BNM signals NDF crackdown after Trump-victory ringgit slide. Foreign equity flows turn negative.",
    category: "policy",
  },
  {
    date: "2018-05-09",
    label: "GE14 / Pakatan win",
    blurb: "Barisan Nasional ousted after 61 years. KLCI -3% next session, then -7% over the month on policy uncertainty.",
    category: "structural",
  },
  {
    date: "2020-03-09",
    label: "COVID crash begins",
    blurb: "Oil price war (Saudi-Russia) plus COVID lockdowns. KLCI -23% peak to trough by March 19.",
    category: "crisis",
  },
  {
    date: "2020-03-23",
    label: "Bursa circuit breaker low",
    blurb: "MCO declared March 18; KLCI hits 1219 low. Beginning of OOS holdout's first stress test.",
    category: "crisis",
  },
  {
    date: "2021-08-16",
    label: "PM Ismail Sabri / political reset",
    blurb: "Third PM in 18 months. Brief uncertainty before fiscal/budget continuity reassures markets.",
    category: "structural",
  },
];
