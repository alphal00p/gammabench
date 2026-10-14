#import "/src/gammabench/assets/components.typ": *
#import "/src/gammabench/assets/matrix.typ": comparison-matrix
#let studies = json("studies.json")
#let analyses = studies.map(study => json("/docs/results/" + study.id + "/analysis.json"))
#let titles = (:)
#for study in studies { titles.insert(study.id, study.title) }
#let overview = comparison-matrix(
  analyses,
  titles: titles,
  initial-study: "generation-materialized-20261012",
)

#document("index.html")[
  #bench-document(
    "Benchmark results",
    "Browse versioned GammaLoop benchmarks, compare equivalent outputs, and inspect measurement evidence.",
    assets: "assets/", home: "index.html", interactive: true,
  )[
    #hero(compact: true)[
      #kicker[Public research · captured measurements]

      = Compare benchmark results

      Choose the output, metric and reference. Each row compares the same workload; coloured cells have a qualified comparison against the reference.
    ]
    #section[#overview]
    #section[
      #el("details")[
        #el("summary")[Study reports and evidence]
        #study-cards(studies, "docs/results/")
        Each study retains its original measurements, output contract, tool versions and qualification evidence.

        #link("artifacts.json")[Artifact index] · #link("https://github.com/alphal00p/gammabench/releases/tag/v0.1.0")[Download raw evidence]
      ]
    ]
  ]
]

#document("docs/results/index.html")[
  #bench-document("Preserved results", "Historical generation studies with explicit output and measurement contracts.", assets: "../../assets/", home: "../../index.html", interactive: true)[
    #hero(compact: true)[
      #kicker[Captured measurements · source evidence]

      = Compare preserved results

      Select the output and reference before comparing workloads.
    ]
    #section[#overview]
    #section[
      #el("details")[
        #el("summary")[Individual study reports]
        #study-cards(studies, "")
      ]
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
