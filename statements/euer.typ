// Data is passed via sys.inputs.data as a JSON string
#let doc    = json(bytes(sys.inputs.data))
#let labels = doc.at("labels")

#set page(
  paper: "a4",
  margin: (left: 25mm, right: 20mm, top: 35mm, bottom: 40mm),
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

#set text(font: ("Albert Sans", "Liberation Sans", "Helvetica", "Arial"), size: 10pt, weight: "regular")

#let category_table(items, spheres_enabled) = {
  if items.len() == 0 {
    text(fill: gray, labels.at("empty"))
  } else if spheres_enabled {
    table(
      columns: (3fr, 2fr, 1fr),
      align: (left, left, right),
      row-gutter: 0.4em,
      column-gutter: 0.5em,
      stroke: none,
      table.header(
        text(weight: "bold", labels.at("col_category")),
        text(weight: "bold", labels.at("col_sphere")),
        text(weight: "bold", labels.at("col_amount")),
        table.hline(),
      ),
      ..items.map(row => (row.at("name"), row.at("sphere_label"), row.at("line_amount"))).flatten(),
    )
  } else {
    table(
      columns: (4fr, 1fr),
      align: (left, right),
      row-gutter: 0.4em,
      column-gutter: 0.5em,
      stroke: none,
      table.header(
        text(weight: "bold", labels.at("col_category")),
        text(weight: "bold", labels.at("col_amount")),
        table.hline(),
      ),
      ..items.map(row => (row.at("name"), row.at("line_amount"))).flatten(),
    )
  }
}

#heading(level: 2, labels.at("section_income"))
#v(0.5em)
#category_table(doc.at("income_items"), doc.at("spheres_enabled"))
#v(0.5em)
#table(
  columns: (4fr, 1fr), stroke: none, column-gutter: 0.5em,
  table.hline(),
  text(weight: "bold", labels.at("total_income")), text(weight: "bold", doc.at("total_income")),
)

#v(1.5em)

#heading(level: 2, labels.at("section_expense"))
#v(0.5em)
#category_table(doc.at("expense_items"), doc.at("spheres_enabled"))
#v(0.5em)
#table(
  columns: (4fr, 1fr), stroke: none, column-gutter: 0.5em,
  table.hline(),
  text(weight: "bold", labels.at("total_expense")), text(weight: "bold", doc.at("total_expense")),
)

#v(2em)

#table(
  columns: (4fr, 1fr),
  align: (left, right),
  stroke: none,
  column-gutter: 0.5em,
  table.hline(stroke: 1pt),
  text(weight: "bold", size: 12pt, labels.at("net_result")),
  text(weight: "bold", size: 12pt, doc.at("net_result")),
  table.hline(stroke: 1pt),
)

#v(1em)
#text(size: 8pt, fill: gray, labels.at("disclaimer"))
