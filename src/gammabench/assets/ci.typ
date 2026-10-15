#import "components.typ": el, bench-document, hero, section
#let data = json("ci.json")
#let outcome(status) = if status == none { "" } else { ("Passed", "Regression or correctness failure", "Inconclusive").at(status) }
#bench-document(data.title, "Paired CI measurements and retained evidence", assets: data.assets, home: data.home)[
  #hero(compact: true)[
    = #data.title
    #if data.at("preview", default: false) { el("p", class: "notice")[Premerge measurements · separate from main history] }
    #if data.at("status", default: none) != none { el("p")[#outcome(data.status)] }
    #if data.at("captured_at", default: "") != "" { el("p")[Captured #data.captured_at] }
    #if data.at("ci", default: none) != none {
      let ci = data.ci
      el("p")[
        Candidate #link(ci.repository_url + "/commit/" + ci.candidate)[#ci.candidate.slice(0, 12)] ·
        Baseline #link(ci.repository_url + "/commit/" + ci.baseline)[#ci.baseline.slice(0, 12)] ·
        #link(ci.repository_url + "/actions/runs/" + str(ci.run_id))[Actions run]
        #if ci.at("pr", default: none) != none { [ · #link(ci.repository_url + "/pull/" + str(ci.pr))[Pull request]] }
      ]
    }
  ]
  #section[
    #for row in data.rows {
      el("article", class: "notice")[
        #if row.href != "" { link(row.href, row.title) } else { row.title }
        #if row.status != none { [ · #outcome(row.status)] }
        #if row.candidate != "" { el("p")[Measured #row.candidate.slice(0, 12) against #row.baseline.slice(0, 12)] }
      ]
    }
    #if data.rows.len() == 0 { [No measurements have been published yet.] }
  ]
]
