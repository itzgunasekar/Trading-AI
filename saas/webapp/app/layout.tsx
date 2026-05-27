import type { Metadata } from "next";
import "./globals.css";
import { Inter } from "next/font/google";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "D1 Portfolio — Institutional-grade algorithmic trading",
  description:
    "12 strategies × 21 instruments. 8-year out-of-sample validated. Runs on your own broker account.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="text-slate-100 min-h-screen antialiased font-sans">
        <Nav />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
