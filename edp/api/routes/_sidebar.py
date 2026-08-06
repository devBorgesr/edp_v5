"""
_sidebar.py — Componente de barra lateral compartilhado entre as 3 páginas do EDP.

Uso:
    from ._sidebar import SIDEBAR_CSS, render_sidebar

    html = "<head>...{}...</head>".format(SIDEBAR_CSS)
    html += render_sidebar(current_page="memory")

Páginas válidas: dashboard | memory | flags
"""

SIDEBAR_CSS = """
<style>
/* ── Sidebar recolhível ───────────────────────────────────────── */
.edp-sb-toggle {
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 1001;
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 6px;
  width: 38px;
  height: 38px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  padding: 0;
  transition: background 0.15s;
}
.edp-sb-toggle:hover { background: #334155; }
.edp-sb-toggle .bar {
  display: block;
  width: 18px;
  height: 2px;
  background: #e2e8f0;
  margin: 3px auto;
  border-radius: 1px;
  transition: transform 0.2s, opacity 0.2s;
}
body.edp-sb-open .edp-sb-toggle .bar1 { transform: translateY(5px) rotate(45deg); }
body.edp-sb-open .edp-sb-toggle .bar2 { opacity: 0; }
body.edp-sb-open .edp-sb-toggle .bar3 { transform: translateY(-5px) rotate(-45deg); }

.edp-sb-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 999;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
}
body.edp-sb-open .edp-sb-overlay {
  opacity: 1;
  pointer-events: auto;
}

.edp-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: 230px;
  height: 100vh;
  background: #0f172a;
  border-right: 1px solid #334155;
  z-index: 1000;
  transform: translateX(-100%);
  transition: transform 0.22s ease-out;
  display: flex;
  flex-direction: column;
  padding: 64px 0 16px 0;
  box-shadow: 2px 0 12px rgba(0,0,0,0.3);
}
body.edp-sb-open .edp-sidebar {
  transform: translateX(0);
}

.edp-sb-header {
  padding: 0 20px 16px 20px;
  border-bottom: 1px solid #1e293b;
  margin-bottom: 12px;
}
.edp-sb-title {
  color: #06b6d4;
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  letter-spacing: 0.3px;
}
.edp-sb-subtitle {
  color: #64748b;
  font-size: 11px;
  margin-top: 2px;
}

.edp-sb-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px;
}
.edp-sb-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  color: #cbd5e1;
  text-decoration: none;
  font-size: 13px;
  border-radius: 5px;
  transition: background 0.12s, color 0.12s;
}
.edp-sb-link:hover {
  background: #1e293b;
  color: #e2e8f0;
}
.edp-sb-link.active {
  background: #1e293b;
  color: #06b6d4;
  font-weight: 500;
}
.edp-sb-link.active::before {
  content: '';
  width: 3px;
  height: 16px;
  background: #06b6d4;
  border-radius: 2px;
  margin-left: -14px;
  margin-right: 7px;
}
.edp-sb-icon {
  font-size: 15px;
  width: 18px;
  display: inline-flex;
  justify-content: center;
}

.edp-sb-footer {
  margin-top: auto;
  padding: 12px 20px;
  border-top: 1px solid #1e293b;
  color: #475569;
  font-size: 10px;
}
</style>
"""

SIDEBAR_JS = """
<script>
(function() {
  function toggleSidebar() {
    document.body.classList.toggle('edp-sb-open');
  }
  function closeSidebar() {
    document.body.classList.remove('edp-sb-open');
  }
  window.addEventListener('DOMContentLoaded', function() {
    const btn = document.getElementById('edp-sb-toggle');
    const ov  = document.getElementById('edp-sb-overlay');
    if (btn) btn.addEventListener('click', toggleSidebar);
    if (ov)  ov.addEventListener('click', closeSidebar);
    // Fecha com Esc
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closeSidebar();
    });
  });
})();
</script>
"""


def render_sidebar(current_page: str = "") -> str:
    """
    Renderiza HTML completo: botão toggle + overlay + sidebar.

    Args:
        current_page: 'dashboard' | 'memory' | 'flags' (para highlight)
    """
    links = [
        ("dashboard", "/dashboard",      "Dashboard",      "📊"),
        ("memory",    "/memory/review",  "Memory Review",  "🧠"),
        ("flags",     "/flags/review",   "Flags Review",   "🚩"),
        ("graph",     "/graph",          "Grafo",          "🕸️"),
    ]

    nav_items = []
    for key, href, label, icon in links:
        active = " active" if key == current_page else ""
        nav_items.append(
            f'<a class="edp-sb-link{active}" href="{href}">'
            f'<span class="edp-sb-icon">{icon}</span>{label}</a>'
        )
    nav_html = "\n      ".join(nav_items)

    return f"""
<button id="edp-sb-toggle" class="edp-sb-toggle" aria-label="Abrir menu" title="Menu (Esc para fechar)">
  <span><span class="bar bar1"></span><span class="bar bar2"></span><span class="bar bar3"></span></span>
</button>

<div id="edp-sb-overlay" class="edp-sb-overlay"></div>

<aside class="edp-sidebar" aria-label="Navegação">
  <div class="edp-sb-header">
    <h2 class="edp-sb-title">EDP v3.5</h2>
    <div class="edp-sb-subtitle">Cognitive Runtime</div>
  </div>
  <nav class="edp-sb-nav">
      {nav_html}
  </nav>
  <div class="edp-sb-footer">
    Esc para fechar
  </div>
</aside>
"""
