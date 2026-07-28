import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

const geist = localFont({
  src: "./fonts/Geist[wght].ttf",
  weight: "100 900",
  variable: "--font-geist",
  display: "swap",
  adjustFontFallback: "Arial",
});

const instrumentSerif = localFont({
  src: "./fonts/InstrumentSerif-Regular.ttf",
  weight: "400",
  style: "normal",
  variable: "--font-instrument-serif",
  display: "swap",
  adjustFontFallback: "Times New Roman",
});

const jetbrainsMono = localFont({
  src: "./fonts/JetBrainsMono[wght].ttf",
  weight: "100 800",
  variable: "--font-jetbrains-mono",
  display: "swap",
  fallback: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
  adjustFontFallback: false,
});

// Display face for the SmartRoute Left Rail — paired with JetBrains Mono for
// meta caps. Loaded weights match the left-rail prototype (300–700 used across
// headlines, line names, station names, and numeric counts).
const spaceGrotesk = localFont({
  src: "./fonts/SpaceGrotesk[wght].ttf",
  weight: "300 700",
  variable: "--font-space-grotesk",
  display: "swap",
  adjustFontFallback: "Arial",
});

// The left rail's single grotesk family. Variable font so the rail's
// fractional weights (560/620/650) interpolate instead of snapping to Arial.
// Stands in for Helvetica, the mandated NYCTA signage face — swap a licensed
// Helvetica Now in front of it in --sr-display without touching components.
const archivo = localFont({
  src: "./fonts/Archivo[wdth,wght].ttf",
  weight: "100 900",
  variable: "--font-archivo",
  display: "swap",
  adjustFontFallback: "Arial",
});

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0d1117",
};

export const metadata: Metadata = {
  applicationName: "SmartRoute",
  title: "SmartRoute",
  description: "Real-time NYC transit intelligence",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "SmartRoute",
  },
  icons: {
    icon: [
      {
        url: "/smart-route-app-icon-16.png",
        sizes: "16x16",
        type: "image/png",
      },
      {
        url: "/smart-route-app-icon-32.png",
        sizes: "32x32",
        type: "image/png",
      },
      {
        url: "/smart-route-app-icon-192.png",
        sizes: "192x192",
        type: "image/png",
      },
    ],
    shortcut: "/favicon.ico",
    apple: {
      url: "/smart-route-app-icon-180.png",
      sizes: "180x180",
      type: "image/png",
    },
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geist.variable} ${instrumentSerif.variable} ${jetbrainsMono.variable} ${spaceGrotesk.variable} ${archivo.variable} antialiased`}
        style={{
          fontFamily: "var(--font-geist), system-ui, -apple-system, sans-serif",
        }}
      >
        {children}
        <Analytics />
      </body>
    </html>
  );
}
