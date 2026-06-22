import type { Metadata, Viewport } from "next";
import {
  Geist,
  Instrument_Serif,
  JetBrains_Mono,
  Space_Grotesk,
} from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

const geist = Geist({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-geist",
});

const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: ["400"],
  style: ["normal", "italic"],
  variable: "--font-instrument-serif",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-jetbrains-mono",
});

// Display face for the SmartRoute Left Rail — paired with JetBrains Mono for
// meta caps. Loaded weights match the left-rail prototype (300–700 used across
// headlines, line names, station names, and numeric counts).
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-space-grotesk",
});

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export const metadata: Metadata = {
  title: "ATLAS",
  description: "Personal Transit Intelligence",
  icons: {
    icon: "/smart-route-mark.png",
    shortcut: "/smart-route-mark.png",
    apple: "/smart-route-mark.png",
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
        className={`${geist.variable} ${instrumentSerif.variable} ${jetbrainsMono.variable} ${spaceGrotesk.variable} antialiased`}
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
