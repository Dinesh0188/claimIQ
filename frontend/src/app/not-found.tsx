import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <h1 className="text-4xl font-bold text-muted-2 mb-2">404</h1>
      <h2 className="text-xl font-semibold mb-4">Page not found</h2>
      <p className="text-muted mb-6 max-w-md">
        The page you are looking for does not exist or has been moved.
      </p>
      <Link
        href="/"
        className="inline-flex items-center px-4 py-2 bg-accent hover:bg-accent-deep text-white rounded-md text-sm font-medium transition-colors"
      >
        Check a claim
      </Link>
    </div>
  );
}
