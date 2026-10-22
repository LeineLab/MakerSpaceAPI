// Data is passed via sys.inputs.data as a JSON string
#let doc    = json(bytes(sys.inputs.data))
#let labels = doc.at("labels")

#set page(
  paper: "a4",
  margin: (left: 25mm, right: 20mm, top: 25mm, bottom: 25mm),
  footer: context [
    #set text(8pt)
    #table(
      columns: (1fr, 1fr),
      align: (left, right),
      stroke: none,
      [#labels.at("generated") #datetime.today().display("[day].[month].[year]")],
      [#set align(right); #counter(page).display("1 / 1", both: true)]
    )
  ]
)

#set text(font: ("Albert Sans", "Liberation Sans", "Helvetica", "Arial"), size: 10pt, weight: "regular")
#set par(justify: false, leading: 0.6em)

#let cb(checked) = box(
  width: 9pt, height: 9pt, stroke: 0.6pt, inset: 0pt, baseline: 1pt,
  align(center + horizon, if checked { text(size: 8pt, weight: "bold")[×] }),
)
#let option(checked, body) = [#cb(checked)#h(0.5em)#body \ ]
#let blank(width: 6cm) = box(width: width, stroke: (bottom: 0.5pt), outset: (bottom: 1pt))[#h(1pt)]
#let value(v, width: 6cm) = if v == none or v == "" { blank(width: width) } else { box(stroke: (bottom: 0.5pt), outset: (bottom: 1pt), v) }
#let sec(t) = block(sticky: true, text(weight: "bold", t))
#let note(body) = text(size: 8.5pt, fill: rgb("#555555"), body)

#if doc.at("draft") {
  align(right, text(fill: rgb("#b00020"), weight: "bold", size: 11pt, labels.at("draft_mark")))
}

#text(size: 18pt, weight: "bold", doc.at("title"))
#v(0.3em)
#if doc.at("supplement_of") != none {
  text(weight: "bold", doc.at("supplement_of"))
  v(0.3em)
}
#text(weight: "bold", labels.at("report_for_year")) #value(doc.at("year"), width: 3cm)

#v(0.6em)
#if doc.at("association") != none {
  text(size: 12pt, weight: "bold", doc.at("association"))
}

#v(0.6em)
#labels.at("on_date") #value(doc.at("audit_date"), width: 3cm) #labels.at("audit_happened") \
#labels.at("for_period") #value(doc.at("period_start"), width: 2.6cm) #labels.at("to") #value(doc.at("period_end"), width: 2.6cm) #labels.at("took_place")

#v(0.6em)
#sec(labels.at("auditors_heading"))
#v(0.2em)
#doc.at("auditors")

#v(0.8em)
#sec(labels.at("result_heading"))
#v(0.2em)
#option(not doc.at("has_objections"), labels.at("no_objections"))
#option(doc.at("has_objections"), labels.at("objections"))
#if doc.at("findings") != none and doc.at("findings") != "" {
  block(inset: (left: 1.4em), doc.at("findings"))
} else if doc.at("has_objections") {
  block(inset: (left: 1.4em), blank(width: 12cm))
}

#v(0.8em)
#sec(labels.at("records_heading"))
#v(0.2em)
#labels.at("records_intro") \
#option(doc.at("receipts_complete") == true, labels.at("receipts_complete"))
#option(doc.at("receipts_complete") == false, labels.at("receipts_incomplete"))
#option(doc.at("statements_complete") == true, labels.at("statements_complete"))
#option(doc.at("statements_complete") == false, labels.at("statements_incomplete"))
#option(doc.at("receipts_clearly_marked") == true, labels.at("receipts_clearly_marked"))
#if doc.at("receipts_comment") != none and doc.at("receipts_comment") != "" {
  block(inset: (left: 1.4em), doc.at("receipts_comment"))
}

#v(0.8em)
#sec(labels.at("discharge_heading"))
#v(0.2em)
#option(doc.at("recommends_discharge"), labels.at("discharge_yes"))
#option(not doc.at("recommends_discharge"), labels.at("discharge_no"))
#option(doc.at("next_audit_date") != none, [#labels.at("next_audit") #value(doc.at("next_audit_date"), width: 3cm)])

#v(0.8em)
#sec(labels.at("computed_heading"))
#v(0.2em)
#note[#for line in doc.at("stats_lines") [#line \ ]]
#if doc.at("current_lines").len() > 0 {
  v(0.3em)
  note[#sec(labels.at("current_heading")) \ #for line in doc.at("current_lines") [#line \ ]]
}
#if doc.at("unreviewed_warning") != none {
  v(0.3em)
  text(weight: "bold", fill: rgb("#b00020"), doc.at("unreviewed_warning"))
}

#v(0.8em)
#sec(labels.at("remarks_heading"))
#v(0.2em)
#if doc.at("remarks") != none and doc.at("remarks") != "" {
  doc.at("remarks")
} else {
  option(true, labels.at("no_remarks"))
}

#v(1.6em)
#labels.at("place") #value(doc.at("place"), width: 5cm), #labels.at("date") #value(none, width: 4cm)

#v(2.2em)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 1.5cm,
  [#line(length: 100%, stroke: 0.5pt) #v(-0.4em) #note(labels.at("sign_auditors"))],
  [#line(length: 100%, stroke: 0.5pt) #v(-0.4em) #note(labels.at("sign_auditors"))],
)
#v(1.8em)
#box(width: 8cm)[#line(length: 100%, stroke: 0.5pt) #v(-0.4em) #note(labels.at("sign_treasurer"))]
