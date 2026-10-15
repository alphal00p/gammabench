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

#let bench-document(
  title, description, body,
  assets: "", home: "https://alphal00p.github.io/gammabench/", interactive: false, scripts: (), styles: (),
) = el("html", attrs: (lang: "en"))[
  #el("head")[
    #void("meta", attrs: (charset: "utf-8"))
    #void("meta", attrs: (name: "viewport", content: "width=device-width,initial-scale=1"))
    #void("meta", attrs: (name: "description", content: description))
    #void("meta", attrs: (name: "theme-color", content: "#f9f6f0"))
    #el("title")[#title · gammabench]
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
      #el("a", class: "portal-brand", attrs: (href: home))[
        #el("span", class: "portal-brand-logo", attrs: ("aria-hidden": "true"))[]
        #el("span", class: "portal-brand-copy")[
          #el("strong")[gammabench]
          #el("small")[αLoop benchmark evidence]
        ]
      ]
      #el("nav", class: "portal-nav", attrs: ("aria-label": "Primary"))[
        #link(home)[Explore]
        #link("https://alphal00p.github.io/gammaloop/")[GammaLoop]
        #link("https://github.com/alphal00p/gammabench/blob/main/docs/protocol.md")[Protocol]
      ]
      #el("div", class: "portal-header-actions")[
        #el("a", class: "portal-source-link", attrs: (href: "https://github.com/alphal00p/gammabench"))[Source ↗]
        #el("button", class: "portal-theme-button", attrs: (
          type: "button", "data-theme-toggle": "", "aria-label": "Toggle color theme",
        ))[◐]
      ]
    ]
    #el("main", class: "portal-main bench-main", attrs: (id: "main-content"))[
      #show heading: it => el("h" + str(it.level), it.body)
      #body
    ]
    #el("footer", class: "portal-footer")[
      #el("div")[
        #el("span", class: "portal-footer-mark", attrs: ("aria-hidden": "true"))[]
        #el("p")[#el("strong")[gammabench] #el("br")[] Versioned research benchmarks]
      ]
      #el("nav", attrs: ("aria-label": "Evidence"))[
        #link("https://github.com/alphal00p/gammabench/releases/tag/v0.1.0")[Raw evidence & checksums]
        #link("https://github.com/alphal00p/gammabench/blob/main/docs/protocol.md")[Measurement protocol]
        #link("https://github.com/alphal00p/gammabench")[Source]
      ]
      #el("p")[Original measurements, explicit contracts, and verifiable snapshots.]
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
