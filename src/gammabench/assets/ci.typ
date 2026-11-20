#import "components.typ": el, bench-document, bench-home, hero, section
#let data = json("ci.json")
#let ci = data.at("ci", default: none)
#let tracker = data.at("tracker", default: data.at("local_tracker", default: none))
#let outcome(status) = if status == none { "" } else { ("Passed", "Regression or correctness failure", "Inconclusive").at(status) }
#bench-document(data.title, "Paired CI measurements and retained evidence", assets: data.assets, home: data.home,
  local-performance: if tracker != none { tracker.split("?").first() },
  previews: data.at("previews_url", default: bench-home + "previews/"),
  run-id: if ci != none { str(ci.run_id) + "-" + str(ci.run_attempt) },
  current: if ci == none and data.at("preview", default: false) { "Pull requests" } else { "Performance tracker" },
  head: if data.at("redirect", default: none) != none {
    el("meta", attrs: ("http-equiv": "refresh", content: "0;url=" + data.redirect), [])
  } else { [] },
)[
  #hero(compact: true)[
    = #data.title
    #if data.at("preview", default: false) { el("p", class: "notice")[Premerge measurements · separate from main history] }
    #if data.at("status", default: none) != none { el("p", data.at("headline", default: outcome(data.status))) }
    #if data.at("captured_at", default: "") != "" { el("p")[Captured #data.captured_at] }
    #if data.at("ci", default: none) != none {
      el("p")[
        Candidate #link(ci.repository_url + "/commit/" + ci.candidate)[#ci.candidate.slice(0, 12)] ·
        Baseline #link(ci.repository_url + "/commit/" + ci.baseline)[#ci.baseline.slice(0, 12)] ·
        #link(ci.repository_url + "/actions/runs/" + str(ci.run_id) + "/attempts/" + str(ci.run_attempt))[Actions run]
        #if ci.at("pr", default: none) != none { [ · #link(ci.repository_url + "/pull/" + str(ci.pr))[Pull request]] }
      ]
    }
  ]
  #section[
    #if data.at("redirect", default: none) != none { el("p", link(data.redirect, [Open run results →])) }
    #if data.at("tracker", default: none) != none { el("p", link(data.tracker, [Open performance tracker →])) }
    #for row in data.rows {
      el("article", class: "notice")[
        #if row.href != "" { link(row.href, row.title) } else { row.title }
        #if row.status != none { [ · #row.at("outcome", default: outcome(row.status))] }
        #if row.at("detail", default: "") != "" { el("p", row.detail) }
        #if row.candidate != "" and (data.at("ci", default: none) == none or row.at("comparison", default: "revision") == "within-candidate") {
          if row.at("comparison", default: "revision") == "within-candidate" {
            el("p")[Comparison within candidate #row.candidate.slice(0, 12) · both sides use this commit]
          } else {
            el("p")[Measured #row.candidate.slice(0, 12) against #row.baseline.slice(0, 12)]
          }
        }
      ]
    }
    #if data.at("include_published_link", default: false) {
      el("p")[This build includes main history. #link(bench-home + "previews/")[Browse published PR previews.]]
    } else if data.rows.len() == 0 { [No measurements have been published yet.] }
  ]
]
