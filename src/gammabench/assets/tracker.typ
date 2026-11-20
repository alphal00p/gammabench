#import "components.typ": bench-document, bench-home, el
#import "matrix.typ": matrix-metric
#let data = json("tracker.json")
#let metric-labels = (:)
#for run in data.runs {
  for case in run.cases {
    for name in case.at("flagged_metrics", default: case.at("nonpassing_metrics", default: (:))).keys() {
      metric-labels.insert(name, matrix-metric(name))
    }
  }
}
#let control(label, key) = el("label", class: "tracker-select", {
  label
  el("select", attrs: (id: "tracker-" + key))[]
})
#let button(label, key, value) = el("button", attrs: (type: "button", (key): value), label)
#let table(columns, key) = el("table")[
  #el("thead")[#el("tr")[#for label in columns { el("th", attrs: (scope: "col"), label) }]]
  #el("tbody", attrs: (id: "tracker-" + key))[]
]
#bench-document(data.title, "Performance history, paired comparisons and run evidence",
  assets: data.assets, home: data.home,
  previews: data.at("previews_url", default: bench-home + "previews/"),
  performance: if data.preview { "../../../../ci-history/index.html" } else { "index.html" },
  current: "Performance tracker", local-performance: "index.html", run-id: data.selected_run,
  styles: ("tracker.css",), scripts: ("tracker.js",))[
  #el("div", class: "tracker", attrs: (id: "performance-tracker", "data-model": read("tracker.json"), "data-metric-labels": json.encode(metric-labels, pretty: false)))[
    #el("section", class: "tracker-section")[
      #el("div", class: "tracker-kicker", {
        el("span", attrs: (id: "tracker-scope"), if data.preview { "GammaLoop · PR #" + str(data.pr) } else { "GammaLoop · main" })
        el("a", attrs: (id: "tracker-back", hidden: ""))[Run results]
      })
      #el("h1", attrs: (id: "tracker-title"), data.title)
      #el("div", class: "tracker-options", attrs: (id: "tracker-options", hidden: ""), {
        control("Workload", "workload")
        control("Run (UTC)", "run")
        el("span", attrs: (id: "tracker-context"))[]
        el("span", attrs: (id: "tracker-selected-status"))[]
      })
    ]
    #el("div", attrs: (id: "tracker-history", hidden: ""))[
      #el("section", class: "tracker-section")[
        #el("div", class: "tracker-row")[
          #el("h2")[Generation time]
          #el("div", class: "tracker-segment", attrs: (role: "group", "aria-label": "Chart measure"))[
            #button("Seconds", "data-mode", "seconds")
            #button("Paired change", "data-mode", "delta")
          ]
        ]
        #el("p", class: "tracker-note", attrs: (id: "tracker-measure"))[]
        #el("div", class: "tracker-chart", attrs: (id: "tracker-chart"))[]
        #el("p", class: "tracker-note", attrs: (id: "tracker-axis"))[]
      ]
      #el("section", class: "tracker-section")[
        #el("h2")[Run history]
        #table(("Run (UTC)", "Generation change", "Case result"), "history-rows")
        #el("p", class: "tracker-note")[Median paired change from confirmation when available, otherwise discovery.]
        #el("p", class: "tracker-detail", attrs: (id: "tracker-detail", "aria-live": "polite"))[]
      ]
    ]
    #el("section", class: "tracker-section", attrs: (id: "tracker-results", "aria-label": "Workload results", hidden: ""))[
      #el("p", class: "tracker-summary")[
        #el("span", attrs: (id: "tracker-total"))[] · #el("span", attrs: (id: "tracker-counts"))[]
      ]
      #table(("Workload", "Generation change", "Case result"), "suite-rows")
      #el("p", class: "tracker-note")[Median paired change from confirmation when available, otherwise discovery. Case results include every gated metric.]
    ]
    #el("section", class: "tracker-section", attrs: (id: "tracker-fallback"))[
      #if data.runs.len() == 0 {
        el("p")[No CI captures have been published here yet.]
      } else {
        el("h2")[Recorded runs]
        el("ul")[#for run in data.runs.rev() {
          el("li", link(run.url, run.sha.slice(0, 8) + " · run " + run.id + " · " + run.status))
        }]
      }
    ]
    #el("div", class: "tracker-section tracker-links")[
      #el("span", class: "tracker-note", attrs: (id: "tracker-origin"))[]
      #el("a", attrs: (id: "tracker-evidence", hidden: ""))[Run evidence]
      #el("a", attrs: (id: "tracker-actions", hidden: ""))[Actions run]
      #if data.preview {
        link("../../runs.html")[All runs for this PR]
      }
    ]
  ]
]
