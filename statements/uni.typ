// Data is passed via sys.inputs.data as a JSON string
#let doc    = json(bytes(sys.inputs.data))
#let items  = doc.at("items")
#let labels = doc.at("labels")

#set page(
  paper: "a4",
  margin: (left: 25mm, right: 20mm, top: 55mm, bottom: 40mm),
  header: [
    #table(
      columns: (3fr, 1fr),
      align: (left, right),
      stroke: none,
      gutter: 0mm,
      inset: 0mm,
    [
      #heading(doc.at("title"))
      #heading(
        level: 2,
        labels.at("period_prefix") + " " +
        doc.at("period_start") + " " +
        labels.at("period_connector") + " " +
        doc.at("period_end"))
    ],
    [
      #set align(right)
      #image("logo.svg", width: 30mm)
      #v(-7mm)
    ]
    )
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

#heading(level: 2, labels.at("section_summary"))

#v(1em)

#table(
  columns: (4fr, 1fr),
  align: (left, right),
  row-gutter: 0em,
  column-gutter: 0.5em,
  stroke: none,
  table.hline(),
  text(weight: "bold", labels.at("balance_old") + " " + doc.at("period_start")), doc.at("balance_old"),
  text(weight: "bold", labels.at("sum_inflows")), doc.at("sum_inflows"),
  text(weight: "bold", labels.at("sum_outflows")), doc.at("sum_outflows"),
  text(weight: "bold", labels.at("balance_new") + " " + doc.at("period_end")), doc.at("balance_new"),
  table.hline(),
)

#v(2em)

#heading(level: 2, labels.at("section_transactions"))

#v(1em)

#table(
  columns: (1.5fr, 4fr, 1fr),
  align: (left, left, right),
  row-gutter: 0.5em,
  column-gutter: 0.5em,
  stroke: none,
  table.header(
    text(weight: "bold", labels.at("col_timestamp")),
    text(weight: "bold", labels.at("col_description")),
    text(weight: "bold", labels.at("col_amount")),
    table.hline(),
  ),
  ..items.map(
    row => (
      row.at("timestamp"),
      grid(gutter: 1em, row.at("description")),
      if row.at("line_amount").contains("-") {
        text(fill: red, row.at("line_amount"))
      } else {
        row.at("line_amount")
      }
    )
  ).flatten(),
)
