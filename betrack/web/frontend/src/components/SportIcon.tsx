import footballUrl from "../assets/sports/football.svg";
import basketballUrl from "../assets/sports/basketball.svg";
import tennisUrl from "../assets/sports/tennis.svg";

interface Props {
  sport: string;
  className?: string;
}

const SOURCES: Record<string, string> = {
  football: footballUrl,
  basketball: basketballUrl,
  tennis: tennisUrl,
};

// Sport icon rendered from an SVG asset bundled by Vite. We render via <img>
// instead of inlining the SVG so each icon keeps its original colors/strokes
// from svgrepo — these are illustration-style and don't tint cleanly with
// currentColor.
export function SportIcon({ sport, className = "w-6 h-6" }: Props) {
  const src = SOURCES[(sport || "").toLowerCase()];
  if (!src) {
    return <span className={`${className} inline-block`} aria-hidden />;
  }
  return <img src={src} alt={sport} className={className} draggable={false} />;
}
