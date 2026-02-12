let lastPredicted = null;
let lastRawText = "";
let sourceDocumentId = null;

const $ = (id) => document.getElementById(id);

function readItems() {
  const rows = [...document.querySelectorAll("#items-body tr")];
  return rows
    .map((row) => {
      const inputs = row.querySelectorAll("input");
      const name = inputs[0].value.trim();
      if (!name) return null;
      return {
        name,
        quantity: Number(inputs[1].value) || null,
        unit_price: Number(inputs[2].value) || null,
        total_price: Number(inputs[3].value) || null,
      };
    })
    .filter(Boolean);
}

function fillItems(items) {
  const body = $("items-body");
  body.innerHTML = "";
  const rows = items.length ? items : [{}, {}, {}];
  rows.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input value="${item.name ?? ""}"></td>
      <td><input value="${item.quantity ?? ""}"></td>
      <td><input value="${item.unit_price ?? ""}"></td>
      <td><input value="${item.total_price ?? ""}"></td>`;
    body.appendChild(tr);
  });
}

function collectDoc() {
  return {
    id: sourceDocumentId || crypto.randomUUID(),
    filename: $("filename").value,
    document_type: $("document_type").value,
    supplier: {
      name: $("supplier_name").value,
      inn: $("supplier_inn").value,
      kpp: $("supplier_kpp").value,
      address: $("supplier_address").value,
    },
    buyer: {
      name: $("buyer_name").value,
      inn: $("buyer_inn").value,
      kpp: $("buyer_kpp").value,
      address: $("buyer_address").value,
    },
    items: readItems(),
    signers: [],
    raw_text_excerpt: lastRawText,
    created_at: new Date().toISOString(),
  };
}

function fillDoc(doc) {
  sourceDocumentId = doc.id;
  $("document_type").value = doc.document_type || "Неизвестно";
  $("filename").value = doc.filename || "";
  $("supplier_name").value = doc.supplier?.name || "";
  $("supplier_inn").value = doc.supplier?.inn || "";
  $("supplier_kpp").value = doc.supplier?.kpp || "";
  $("supplier_address").value = doc.supplier?.address || "";
  $("buyer_name").value = doc.buyer?.name || "";
  $("buyer_inn").value = doc.buyer?.inn || "";
  $("buyer_kpp").value = doc.buyer?.kpp || "";
  $("buyer_address").value = doc.buyer?.address || "";
  fillItems(doc.items || []);
  lastRawText = doc.raw_text_excerpt || "";
}

async function refreshLists() {
  const docs = await fetch("/documents").then((r) => r.json());
  const dList = $("documents-list");
  dList.innerHTML = "";
  docs.forEach((d) => {
    const li = document.createElement("li");
    li.textContent = `${d.filename} — ${d.document_type}`;
    li.style.cursor = "pointer";
    li.onclick = () => fillDoc(d);
    dList.appendChild(li);
  });

  const samples = await fetch("/training/samples").then((r) => r.json());
  const tList = $("training-list");
  tList.innerHTML = "";
  samples.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = `${s.filename} — ${new Date(s.created_at).toLocaleString()}`;
    tList.appendChild(li);
  });
}

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("pdf-file").files[0];
  if (!file) return;
  $("status").textContent = "Распознаю документ...";

  const formData = new FormData();
  formData.append("file", file);
  const doc = await fetch("/documents/upload", { method: "POST", body: formData }).then((r) => r.json());
  lastPredicted = doc;
  fillDoc(doc);
  $("status").textContent = "Готово. Проверьте и при необходимости исправьте поля ниже.";
  await refreshLists();
});

$("save-training").addEventListener("click", async () => {
  if (!lastPredicted) {
    alert("Сначала загрузите и распознайте документ.");
    return;
  }
  const corrected = collectDoc();
  const payload = {
    filename: corrected.filename,
    source_document_id: sourceDocumentId,
    raw_text_excerpt: lastRawText,
    predicted: lastPredicted,
    corrected,
    reviewer_comment: $("reviewer_comment").value || "",
  };

  await fetch("/training/samples", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  alert("Сохранено в обучающий набор");
  await refreshLists();
});

$("load-docs").addEventListener("click", refreshLists);
refreshLists();
