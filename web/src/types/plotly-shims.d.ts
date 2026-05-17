// Type shim: react-plotly.js can be fed any plotly.js bundle. We use the
// `-basic-dist-min` build for size; declare it as a value-only module to
// satisfy `import` at runtime, while pulling types from the parent `plotly.js`
// package via direct `import type` statements in chart components.
declare module "plotly.js-basic-dist-min";
