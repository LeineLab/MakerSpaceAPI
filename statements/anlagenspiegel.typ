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

#if rows.len() == 0 {
  text(fill: gray, labels.at("empty"))
} else {
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
    ..rows.map(r => (
      r.at("name"), r.at("category_name"), r.at("acquisition_cost"), r.at("opening_value"),
      r.at("zugang"), r.at("abgang"), r.at("afa"), r.at("closing_value"),
    )).flatten(),
    table.hline(stroke: 1pt),
    text(weight: "bold", labels.at("row_total")), [],
    text(weight: "bold", doc.at("totals").at("acquisition_cost")),
    text(weight: "bold", doc.at("totals").at("opening_value")),
    text(weight: "bold", doc.at("totals").at("zugang")),
    text(weight: "bold", doc.at("totals").at("abgang")),
    text(weight: "bold", doc.at("totals").at("afa")),
    text(weight: "bold", doc.at("totals").at("closing_value")),
  )
}

#v(1em)
#text(size: 8pt, fill: gray, labels.at("disclaimer"))
