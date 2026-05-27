"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { useState, useEffect } from "react";

export default function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <motion.header
      initial={{ y: -24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      className={`fixed top-0 inset-x-0 z-50 transition-all ${
        scrolled ? "glass-strong border-b border-white/5" : ""
      }`}
    >
      <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 font-bold">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-emerald-400 via-cyan-500 to-purple-500 grid place-items-center">
            <span className="text-slate-950 font-black text-sm">D1</span>
          </div>
          <span className="text-base tracking-tight">Portfolio</span>
        </Link>

        <nav className="hidden md:flex items-center gap-8 text-sm text-slate-300">
          <Link href="/#how-it-works" className="hover:text-white transition">
            How it works
          </Link>
          <Link href="/#strategies" className="hover:text-white transition">
            Strategies
          </Link>
          <Link href="/#pricing" className="hover:text-white transition">
            Pricing
          </Link>
          <Link href="/#security" className="hover:text-white transition">
            Security
          </Link>
        </nav>

        <div className="flex items-center gap-3">
          <Link href="/login" className="text-sm text-slate-300 hover:text-white transition">
            Sign in
          </Link>
          <Link href="/signup" className="btn-primary text-sm py-2 px-4">
            Apply for beta
          </Link>
        </div>
      </div>
    </motion.header>
  );
}
