import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <div className="space-y-3">
      <h1 className="text-[28px] leading-9 font-[650]">Page not found</h1>
      <Link to="/" className="text-primary underline hover:text-primary-hover">
        Go to the board
      </Link>
    </div>
  );
}
