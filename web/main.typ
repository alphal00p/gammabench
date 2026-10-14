#import "/src/gammabench/assets/components.typ": *
#let studies = json("studies.json")

#document("index.html")[
  #bench-document(
    "Benchmark results",
    "Browse versioned GammaLoop benchmarks, compare equivalent outputs, and inspect measurement evidence.",
    assets: "assets/", home: "index.html",
  )[
    #hero[
      #kicker[Public research · benchmarks]

      = Benchmarks you can inspect.

      Browse captured results, choose a comparison baseline, and follow each number back to its workload, tool versions and raw records.
    ]
    #group(class: "portal-section-heading")[
      #group[
        #kicker[Generation · four studies]

        == Graph generation
      ]
      #el("p")[Four historical studies, 12 workloads per study. Reports retain the original measurements and qualification evidence.]
    ]
    #study-cards(studies, "docs/results/")
    #section[
      == Compare equivalent work

      Each report has its own output contract and timing scopes. Case, baseline, metric and status filters select precomputed, qualified comparisons. Timeouts and unsupported workloads stay visible.

      Observed ranges describe captured samples; they are not confidence intervals. Historical imports are labelled throughout.

      #link("artifacts.json")[Artifact index] · #link("https://github.com/alphal00p/gammabench/releases/tag/v0.1.0")[Download raw evidence]
    ]
  ]
]

#document("docs/results/index.html")[
  #bench-document("Preserved results", "Historical generation studies with explicit output and measurement contracts.", assets: "../../assets/", home: "../../index.html")[
    #hero[
      #kicker[Captured measurements · source evidence]

      = Preserved results

      Historical generation studies displayed through the standard snapshot analysis. Each study retains its own output and measurement contracts.
    ]
    #study-cards(studies, "")
    #section[
      Reports include tables, paired comparisons, versions, contracts, failures and CSV/JSON exports. Different output contracts are not ranked together.
    ]
  ]
]

#document("404.html")[
  #bench-document("Page not found", "Return to the gammabench results catalog.", assets: "/gammabench/assets/", home: "/gammabench/")[
    #hero[
      #kicker[Page not found]

      = That result page could not be found.

      Return to #link("/gammabench/")[gammabench results], or inspect the #link("https://github.com/alphal00p/gammabench")[source repository].
    ]
  ]
]
