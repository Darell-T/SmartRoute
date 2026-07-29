# SmartRoute local font assets

These unmodified font files replace the former `next/font/google` fetches. They
are copied from the authoritative [Google Fonts repository](https://github.com/google/fonts)
at commit `7ff85c87f93ea6cca5f41c69f2e4edcb90240f26`, so builds use only
versioned repository assets.

| Asset | SmartRoute mapping | Variable range | License |
| --- | --- | --- | --- |
| `Geist[wght].ttf` | Application sans, `--font-geist` | 100–900 | [Geist OFL](licenses/Geist-OFL.txt) |
| `JetBrainsMono[wght].ttf` | Numeric and metadata mono, `--font-jetbrains-mono` | 100–800 | [JetBrains Mono OFL](licenses/JetBrainsMono-OFL.txt) |
| `InstrumentSerif-Regular.ttf` | Shell display title, `--font-instrument-serif` | 400 normal | [Instrument Serif OFL](licenses/InstrumentSerif-OFL.txt) |
| `Archivo[wdth,wght].ttf` | Map station labels, `--font-archivo` | width 62–125; weight 100–900 | [Archivo OFL](licenses/Archivo-OFL.txt) |
| `SpaceGrotesk[wght].ttf` | Development left-rail harness, `--font-space-grotesk` | 300–700 | [Space Grotesk OFL](licenses/SpaceGrotesk-OFL.txt) |

The prior Google-loader declarations also requested italic variants for Geist,
Instrument Serif, JetBrains Mono, and Archivo. No current CSS selector uses
those styles, so they are intentionally not shipped.

## Asset checksums

- `Geist[wght].ttf`: `73894E0448CAE90A92B6C2F8732B7BB9ACB7B94C418BFF559DAD4A18E1DE9659`
- `JetBrainsMono[wght].ttf`: `48715A42EC242C21E9F02692891E147D022299A52E48D5E413E1A942193FFEDA`
- `InstrumentSerif-Regular.ttf`: `498EFD461F6DDFCB7A111BF9A565709D2085D48201D501EAD960D93E84FFBB88`
- `Archivo[wdth,wght].ttf`: `0E094A7D3C7C4C25CF1310C4B30014F1DAE9332220B1C2C88F4FA996F0B05053`
- `SpaceGrotesk[wght].ttf`: `ACAD6DE1FC93436F5C0F1F4137751EF04F1AEA3063E7036535970FFCFBD79F72`
