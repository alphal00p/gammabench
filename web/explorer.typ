#import "/src/gammabench/assets/components.typ": el
#import "/src/gammabench/assets/matrix.typ": matrix-select

// Metadata is for discovery; linked reports retain the measurement boundaries.
#let explorer(catalog, root: "", model: none) = {
  assert(catalog.schema_version == 1, message: "Unsupported explorer catalog")
  let attrs = (
    id: "benchmark-explorer", "aria-label": "Benchmark explorer",
    "data-catalog": json.encode(catalog, pretty: false), "data-root": root,
  )
  if model != none { attrs.insert("data-list-model", json.encode(model, pretty: false)) }
  el("section", class: "benchmark-explorer", attrs: attrs)[
    #el("header", class: "explorer-heading")[
      #el("h1")[Benchmarks]
    ]
    #if model != none {
      el("section", class: "list-pinboard", attrs: (id: "list-pins", "aria-label": "Pinned measurements", hidden: ""))[]
    }
    #el("div", attrs: (id: "explorer-controls", hidden: ""))[
      #el("label", class: "explorer-search-label", attrs: ("for": "explorer-keywords"))[Keywords]
      #el("div", class: "explorer-search-area")[
        #el("input", class: "explorer-search", attrs: (
          id: "explorer-keywords", type: "search", autocomplete: "off", maxlength: "500",
          role: "combobox", "aria-autocomplete": "list", "aria-haspopup": "listbox",
          "aria-controls": "explorer-suggestions", "aria-expanded": "false",
          "aria-describedby": "explorer-suggestion-help",
          placeholder: "Search benchmarks, workloads and metadata…",
        ))[]
        #el("ul", class: "explorer-suggestions", attrs: (
          id: "explorer-suggestions", role: "listbox", "aria-label": "Tags, values and fields", hidden: "",
        ))[]
      ]
      #el("span", class: "explorer-visually-hidden", attrs: (id: "explorer-suggestion-help"))[
        Type to suggest declared tags, values and fields. Use arrow keys to select, Enter to choose, and Escape to dismiss. Keywords can also remain plain text.
      ]
      #el("p", class: "explorer-suggestion-feedback", attrs: (id: "explorer-suggestion-feedback", role: "status", hidden: ""))[]
      #el("div", class: "explorer-query-row")[
        #el("div", class: "explorer-chips", attrs: (id: "explorer-chips", "aria-label": "Active filters"))[]
        #el("button", class: "explorer-button", attrs: (
          id: "explorer-add", type: "button", "aria-expanded": "false", "aria-controls": "explorer-builder",
        ), "+ Add filter")
        #el("button", class: "explorer-quiet", attrs: (id: "explorer-clear", type: "button"))[Clear all]
      ]
      #el("form", attrs: (id: "explorer-builder", "aria-label": "Field filter editor", hidden: ""))[
        #el("div", class: "explorer-builder-grid")[
          #matrix-select("explorer-field", "Field", [])
          #matrix-select("explorer-operator", "Condition", [])
          #el("label", attrs: (id: "explorer-value-label"), (el("span", "Value"), el("span", attrs: (id: "explorer-value-slot"), [])).join())
          #el("div", class: "explorer-editor-actions")[
            #el("button", class: "explorer-button explorer-primary", attrs: (id: "explorer-apply", type: "submit"))[Add]
            #el("button", class: "explorer-quiet", attrs: (id: "explorer-cancel", type: "button"))[Cancel]
          ]
        ]
        #el("div", class: "explorer-editor-meta")[
          #el("span", attrs: (id: "explorer-field-help"))[]
          #el("span")[All filters and keywords must match; capture fields use the same configuration.]
        ]
        #el("p", attrs: (id: "explorer-error", role: "alert", hidden: ""))[]
      ]
    ]
    #el("p", attrs: (id: "explorer-notice", role: "status", hidden: ""))[]
    #el("div", class: "explorer-results-bar")[
      #el("span", attrs: (id: "explorer-count", "aria-live": "polite"))[Registered benchmark cases]
      #if model != none {
        el("div", class: "list-views", attrs: (role: "group", "aria-label": "Inline list view"))[
          #for view in ("history", "compare") {
            el("button", class: "explorer-button", attrs: (type: "button", "data-list-view": view,
              "aria-pressed": if view == "history" { "true" } else { "false" }),
              if view == "history" { "History" } else { "Compare" })
          }
        ]
        el("a", attrs: (href: "#list-pins", id: "list-pin-jump", hidden: ""), [Pinned ↑])
      }
      #el("div", attrs: (id: "explorer-group-label", hidden: ""), matrix-select("explorer-group", "Group by", []))
    ]
    #if model != none {
      el("details", class: "list-options list-help", attrs: (id: "list-help", hidden: ""))[
        #el("summary")[Chart key]
        #el("div", attrs: (id: "list-help-content"))[]
      ]
      el("div", class: "list-scope", attrs: (id: "list-scope"))[]
    }
    #el("div", attrs: (id: "explorer-results"))[
      #if catalog.cases.len() == 0 { el("p", class: "explorer-empty")[No benchmark cases are registered yet.] }
      #if catalog.cases.len() > 0 {
        el("div", class: "explorer-table-wrap")[
          #el("table", class: "explorer-table")[
            #el("thead")[#el("tr")[
              #for title in ("Benchmark case", "Benchmark", "Captures") { el("th", attrs: (scope: "col"), title) }
            ]]
            #el("tbody")[
              #for item in catalog.cases {
                el("tr")[
                  #el("td")[#el("a", attrs: (href: root + item.href), item.title) #el("span", class: "explorer-description", item.description)]
                  #el("td", item.benchmark_title)
                  #el("td")[
                    #if item.captures.len() > 0 {
                      link(root + item.captures.first().at("href", default: item.href), "Retained report and evidence")
                    } else { [No captures] }
                  ]
                ]
              }
            ]
          ]
        ]
      }
    ]
    #el("section", attrs: (id: "explorer-inspector", "aria-label": "Selected case metadata", hidden: ""))[]
    #el("noscript")[Search and filters need JavaScript. The registered cases and their evidence links remain available above.]
  ]
}
