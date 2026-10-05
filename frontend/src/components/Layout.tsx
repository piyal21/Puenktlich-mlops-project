import type { ReactNode } from "react";
import { Footer } from "./Footer";
import { TopBar } from "./TopBar";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <a
        href="#main"
        className="sr-only-focusable absolute left-2 top-2 z-50 rounded-md bg-primary px-3 py-2 text-primary-fg"
      >
        Skip to content
      </a>
      <TopBar />
      <main id="main" className="mx-auto w-full max-w-[960px] flex-1 px-4 py-6 md:px-6">
        {children}
      </main>
      <Footer />
    </div>
  );
}
