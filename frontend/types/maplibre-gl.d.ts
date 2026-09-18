// MapLibre 6 exports its ESM bundle, but ships declarations only at the root.
// The explicit bundle path also works in Node tests compiled through CommonJS.
declare module "maplibre-gl/dist/maplibre-gl.mjs" {
  export * from "maplibre-gl";
}
