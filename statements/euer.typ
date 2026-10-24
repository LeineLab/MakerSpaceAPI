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

#let report_table(title, items, income, expense, result, balance_label, result_label) = {
  heading(level: 2, title)
  v(0.5em)
  table(
    columns: (4fr, 1.4fr, 1.4fr),
    align: (left, right, right),
    row-gutter: 0.4em,
    column-gutter: 0.5em,
    stroke: none,
    table.header(
      text(weight: "bold", labels.at("col_category")),
      text(weight: "bold", labels.at("col_income")),
      text(weight: "bold", labels.at("col_expense")),
      table.hline(),
    ),
    ..items.map(row => (row.at("name"), row.at("income_amount"), row.at("expense_amount"))).flatten(),
    table.hline(),
    text(weight: "bold", balance_label),
    text(weight: "bold", income),
    text(weight: "bold", expense),
    text(weight: "bold", result_label),
    text(weight: "bold", result),
    [],
  )
}

#report_table(
  labels.at("section_total"), (), doc.at("total_income"), doc.at("total_expense"), doc.at("net_result"),
  labels.at("overall_balance"), labels.at("overall_result"),
)

#v(1em)
#for sphere in doc.at("spheres") [
  #report_table(
    sphere.at("name"), sphere.at("items"), sphere.at("total_income"), sphere.at("total_expense"), sphere.at("net_result"),
    sphere.at("balance_label"), sphere.at("result_label"),
  )
  #v(1em)
]

#v(1em)
#text(size: 8pt, fill: gray, labels.at("disclaimer"))
