// Data is passed via sys.inputs.data as a JSON string
#let doc    = json(bytes(sys.inputs.data))
#let labels = doc.at("labels")

#set page(
  paper: "a4",
  flipped: true,
  margin: (left: 20mm, right: 20mm, top: 35mm, bottom: 40mm),
  header: [
    #heading(doc.at("title"))
    #heading(level: 2, doc.at("period"))
  ],
  footer: context [
    #set text(8pt)
    #table(
      columns: (1fr, 1fr),
      align: (left, right),
      stroke: none,
      [#labels.at("generated") #datetime.today().display("[day].[month].[year]")],
      [
        #set align(right)
        Page #counter(page).display("1 of 1", both: true)
      ]
    )
  ]
)

#set text(font: ("Albert Sans", "Liberation Sans", "Helvetica", "Arial"), size: 9pt, weight: "regular")

#let rows = doc.at("rows")
#let groups = doc.at("groups")
#let show-groups = doc.at("show_groups")
#let money(t, k) = text(weight: "bold", t.at(k))

#let row-cells(r) = (
  r.at("name"), r.at("category_name"), r.at("acquisition_cost"), r.at("opening_value"),
  r.at("zugang"), r.at("abgang"), r.at("afa"), r.at("closing_value"),
)

#let total-cells(label, t) = (
  text(weight: "bold", label), [],
  money(t, "acquisition_cost"), money(t, "opening_value"), money(t, "zugang"),
  money(t, "abgang"), money(t, "afa"), money(t, "closing_value"),
)

#if rows.len() == 0 {
  text(fill: gray, labels.at("empty"))
} else {
  let body = ()
  if show-groups {
    for g in groups {
      body.push(table.cell(colspan: 8, text(weight: "bold", g.at("label"))))
      body += g.at("rows").map(row-cells).flatten()
      body += total-cells(labels.at("row_subtotal"), g.at("totals"))
    }
  } else {
    body += rows.map(row-cells).flatten()
  }
  table(
    columns: (2fr, 1.5fr, 1fr, 1fr, 1fr, 1fr, 1fr, 1fr),
    align: (left, left, right, right, right, right, right, right),
    row-gutter: 0.4em,
    column-gutter: 0.4em,
    stroke: none,
    table.header(
      text(weight: "bold", labels.at("col_name")),
      text(weight: "bold", labels.at("col_category")),
      text(weight: "bold", labels.at("col_acquisition_cost")),
      text(weight: "bold", labels.at("col_opening_value")),
      text(weight: "bold", labels.at("col_zugang")),
      text(weight: "bold", labels.at("col_abgang")),
      text(weight: "bold", labels.at("col_afa")),
      text(weight: "bold", labels.at("col_closing_value")),
      table.hline(),
    ),
    ..body,
    table.hline(stroke: 1pt),
    ..total-cells(labels.at("row_total"), doc.at("totals")),
  )
}

#v(1em)
#text(size: 8pt, fill: gray, labels.at("disclaimer"))
