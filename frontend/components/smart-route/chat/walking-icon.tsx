import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPersonWalking } from "@fortawesome/free-solid-svg-icons";

export function WalkingIcon({ className }: { className?: string }) {
  return (
    <FontAwesomeIcon
      icon={faPersonWalking}
      className={className}
      aria-hidden="true"
    />
  );
}
