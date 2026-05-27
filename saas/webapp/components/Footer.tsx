export default function Footer() {
  return (
    <footer className="border-t border-white/5 mt-32">
      <div className="max-w-6xl mx-auto px-6 py-10 text-sm text-slate-400 grid md:grid-cols-4 gap-8">
        <div>
          <div className="font-bold text-white mb-2">D1 Portfolio</div>
          <p className="text-xs">
            Beta access only. Trading involves risk of capital loss.
          </p>
        </div>
        <div>
          <div className="text-white font-semibold mb-2">Product</div>
          <ul className="space-y-1">
            <li><a href="/#how-it-works">How it works</a></li>
            <li><a href="/#strategies">Strategies</a></li>
            <li><a href="/#pricing">Pricing</a></li>
          </ul>
        </div>
        <div>
          <div className="text-white font-semibold mb-2">Legal</div>
          <ul className="space-y-1">
            <li><a href="/terms">Terms of Service</a></li>
            <li><a href="/privacy">Privacy Policy</a></li>
            <li><a href="/risk">Risk Disclosure</a></li>
          </ul>
        </div>
        <div>
          <div className="text-white font-semibold mb-2">Support</div>
          <ul className="space-y-1">
            <li><a href="mailto:support@d1portfolio.app">support@d1portfolio.app</a></li>
            <li><a href="/status">Status</a></li>
          </ul>
        </div>
      </div>
      <div className="text-center text-xs text-slate-600 py-6 border-t border-white/5">
        © 2026 D1 Portfolio. Not investment advice. Past performance does not guarantee future results.
      </div>
    </footer>
  );
}
