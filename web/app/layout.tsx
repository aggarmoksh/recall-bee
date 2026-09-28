import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Recall",
  description: "Spaced-retrieval dashboard for Recall",
};

const NAV = [
  { href: "/", label: "Today" },
  { href: "/progress", label: "Progress" },
  { href: "/sessions", label: "Sessions" },
  { href: "/privacy", label: "Privacy" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <header className="bee-hero">
          <div className="blob h-64 w-64 bg-white" style={{ top: "-30%", left: "5%", animationDelay: "0s" }} />
          <div className="blob h-56 w-56 bg-[color:var(--lavender)]" style={{ top: "-20%", right: "10%", animationDelay: "3s" }} />
          <div className="mx-auto flex max-w-5xl items-center justify-between px-8 py-5" style={{ position: "relative" }}>
            <Link href="/" className="bounce inline-block text-xl font-bold tracking-tight text-zinc-900">
              Recall
            </Link>
            <nav className="flex gap-2 text-sm font-medium">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="bounce press rounded-full bg-white/60 px-4 py-2 text-zinc-800 backdrop-blur-sm hover:bg-white/90 hover:shadow-md"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-8 py-10">{children}</main>
      </body>
    </html>
  );
}
