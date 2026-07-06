import Image from "next/image";

type BrandLogoSize = "header" | "compact";
type BrandLogoConnectionState = "connecting" | "open" | "closed";

interface BrandLogoProps {
  size?: BrandLogoSize;
  priority?: boolean;
  connectionState?: BrandLogoConnectionState;
}

const SIZE_STYLES: Record<BrandLogoSize, { height: number }> = {
  header: { height: 36 },
  compact: { height: 28 },
};

export function BrandLogo({
  size = "header",
  priority = true,
  connectionState = "open",
}: BrandLogoProps) {
  const { height } = SIZE_STYLES[size];
  const showSubtitle = size === "header";

  return (
    <div
      aria-label="Smart Route"
      className="sr-brand-logo"
      data-size={size}
      data-connection={connectionState}
      translate="no"
    >
      <Image
        src="/smart-route-mark-tight.png"
        alt=""
        width={height}
        height={height}
        priority={priority}
        aria-hidden="true"
        className="sr-brand-logo__mark"
        style={{
          height,
          width: height,
          display: "block",
        }}
      />
      <span className="sr-brand-logo__copy">
        <span className="sr-brand-logo__wordmark">
          <span className="sr-brand-logo__smart">SMART</span>
          <span className="sr-brand-logo__route">ROUTE</span>
        </span>
        {showSubtitle ? (
          <span className="sr-brand-logo__eyebrow" aria-hidden="true">
            <span className="sr-brand-logo__dot" />
            <span className="sr-brand-logo__eyebrow-text">
              Always The Best Route
            </span>
            <span className="sr-brand-logo__dot" />
          </span>
        ) : null}
      </span>
    </div>
  );
}
