// The semantic HTML boundary follows GammaLoop's portal components. The
// upstream stylesheet owns the palette, typography, header, cards and footer.
#let el(tag, class: none, attrs: (:), body) = {
  let attrs = if class == none { attrs } else { (class: class) + attrs }
  html.elem(tag, attrs: attrs, body)
}
#let void(tag, attrs: (:)) = html.elem(tag, attrs: attrs)
#let group(class: none, body) = el("div", class: class, body)
#let kicker(body) = el("p", class: "portal-kicker", body)
#let hero(compact: false, body) = el("header", class: "portal-page-hero" + if compact { " portal-page-hero-compact" } else { "" }, body)
#let section(body) = el("section", class: "bench-section", body)

#let group-home = "https://www.alphaloop.ch/"
#let bench-home = "https://alphal00p.github.io/gammabench/"

#let brand-art(assets) = {
  for theme in ("light", "dark") {
    void("img", attrs: (
      class: "site-brand-logo site-brand-logo-" + theme + " portal-graph-theme-" + theme,
      src: assets + "theme/alphalooplogo-" + theme + ".svg", alt: "", "aria-hidden": "true",
    ))
  }
}

#let site-links(performance, class: none, include-benchmarks: true) = {
  for (label, href) in (
    ("Documentation", group-home + "libraries/"), ("Benchmarks", performance),
    ("About", group-home + "about/"), ("People", group-home + "people/"),
    ("Talks", group-home + "talks/"), ("Publications", group-home + "publications/"),
  ) {
    if label == "Benchmarks" and not include-benchmarks { continue }
    el("a", class: class, attrs: (href: href) + if label == "Benchmarks" { ("aria-current": "page") } else { (:) }, label)
  }
}

#let bench-navigation(home, performance, tracker, previews, current, run-id) = {
  let run = if run-id == none { "" } else { "&run=" + run-id }
  el("nav", class: "bench-navigation", attrs: (id: "bench-navigation", "aria-label": "Benchmark navigation"))[
    #for (label, href) in (
      ("Performance tracker", tracker + "?view=suite" + run),
      ("Benchmark catalog", home),
      ("Pull requests", previews),
    ) {
      el("a", class: "sidebar-link", attrs: (href: href) + if current == label { ("aria-current": "page") } else { (:) }, label)
    }
    #el("details", class: "bench-site-links")[
      #el("summary")[αLoop website]
      #site-links(performance, class: "sidebar-link", include-benchmarks: false)
    ]
  ]
}

#let bench-document(
  title, description, body,
  assets: "", home: bench-home, performance: bench-home + "ci-history/", current: none,
  local-performance: none, local-explorer: none, previews: bench-home + "previews/", run-id: none,
  interactive: false, scripts: (), styles: (), head: [], search: none,
) = el("html", attrs: (lang: "en"))[
  #el("head")[
    #void("meta", attrs: (charset: "utf-8"))
    #void("meta", attrs: (name: "viewport", content: "width=device-width,initial-scale=1"))
    #void("meta", attrs: (name: "description", content: description))
    #void("meta", attrs: (name: "theme-color", content: "#f9f6f0"))
    #head
    #el("title")[#title · αLoop]
    #void("link", attrs: (rel: "stylesheet", href: assets + "theme/site.css"))
    #void("link", attrs: (rel: "stylesheet", href: assets + "report.css"))
    #for style in styles { void("link", attrs: (rel: "stylesheet", href: assets + style)) }
    #el("script", attrs: (defer: "", src: assets + "theme/site.js"))[]
    #if interactive { el("script", attrs: (defer: "", src: assets + "report.js"))[] }
    #for script in scripts { el("script", attrs: (defer: "", src: assets + script))[] }
  ]
  #el("body", class: "portal-body bench-body")[
    #el("a", class: "skip-link", attrs: (href: "#main-content"))[Skip to content]
    #el("header", class: "portal-header")[
      #el("a", class: "portal-brand", attrs: (href: group-home, "aria-label": "αLoop home"))[#brand-art(assets)]
      #el("nav", class: "portal-nav", attrs: (id: "portal-navigation", "aria-label": "Primary"))[
        #site-links(performance)
      ]
      #el("div", class: "portal-header-actions")[
        #el("a", class: "portal-search-button", attrs: (
          href: if search != none { search } else { home + "#explorer-keywords" },
          "aria-label": "Search benchmarks", title: "Search benchmarks",
        ))[#el("span", class: "header-search-icon", attrs: ("aria-hidden": "true"))[]]
        #el("button", class: "portal-theme-button", attrs: (
          type: "button", "data-theme-toggle": "", "aria-label": "Toggle color theme", title: "Change theme",
        ))[#el("span", attrs: ("aria-hidden": "true"))[◐]]
        #el("button", class: "portal-menu-button", attrs: (
          type: "button", "data-portal-menu-toggle": "", "aria-controls": "bench-navigation",
          "aria-expanded": "false", "aria-label": "Open navigation", title: "Navigation",
        ))[#el("span", attrs: ("aria-hidden": "true"))[☰]]
      ]
    ]
    #el("div", class: "bench-shell")[
      #bench-navigation(
        if local-explorer == none { home } else { local-explorer },
        performance, if local-performance == none { performance } else { local-performance },
        previews, current, run-id,
      )
      #el("main", class: "portal-main bench-main", attrs: (id: "main-content", tabindex: "-1"))[
        #show heading: it => el("h" + str(it.level), it.body)
        #body
      ]
    ]
    #el("footer", class: "portal-footer")[
      #el("div")[
        #brand-art(assets)
      ]
      #el("nav", attrs: ("aria-label": "Evidence"))[
        #link("https://github.com/alphal00p/gammabench/releases/tag/v0.1.0")[Raw evidence & checksums]
        #link("https://github.com/alphal00p/gammabench/blob/main/docs/protocol.md")[Measurement protocol]
        #link("https://github.com/alphal00p/gammabench")[Source]
      ]
      #el("p")[Generated by gammabench.]
    ]
  ]
]

#let study-cards(studies, root) = group(class: "portal-project-grid bench-study-grid")[
  #for (index, study) in studies.enumerate() {
    el("article", class: "portal-project-card")[
      #group(class: "portal-project-meta")[
        #el("span")[#str(index + 1)] #el("span")[#study.kind]
      ]
      === #link(root + study.id + "/index.html")[#study.title]
      #el("p", class: "portal-project-summary")[#study.description]
      #el("nav", class: "portal-card-links", attrs: ("aria-label": study.title))[
        #el("a", class: "portal-card-primary", attrs: (href: root + study.id + "/index.html"))[Browse comparisons →]
      ]
    ]
  }
]
