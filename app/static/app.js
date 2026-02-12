let lastPredicted = null;
let lastRawText = "";
let sourceDocumentId = null;
let pendingFiles = [];

const $ = (id) => document.getElementById(id);

function fileKey(file) {
  return `${file.name}::${file.size}::${file.lastModified}`;
}

function addPendingFiles(files) {
  const onlyPdf = [...files].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
  onlyPdf.forEach((file) => {
    const key = fileKey(file);
    if (!pendingFiles.find((item) => item.key === key)) {
      pendingFiles.push({ key, file });
    }
  });
  renderPendingFiles();
}

function removePendingFile(key) {
  pendingFiles = pendingFiles.filter((item) => item.key !== key);
  renderPendingFiles();
}

function renderPendingFiles() {
  const list = $("pending-files");
  list.innerHTML = "";

  if (!pendingFiles.length) {
    const li = document.createElement("li");
    li.textContent = "Пока нет прикреплённых PDF";
    list.appendChild(li);
    return;
  }

  pendingFiles.forEach((item, index) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="pending-file-name">${index + 1}. ${item.file.name}</span>
      <button type="button" class="remove-file">Убрать</button>
    `;
    li.querySelector(".remove-file").onclick = () => removePendingFile(item.key);
    list.appendChild(li);
  });
}

async function recognizeFile(file) {
  $("status").textContent = `Распознаю: ${file.name}...`;
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/documents/upload", { method: "POST", body: formData });
  if (!response.ok) {
    throw new Error(`Ошибка распознавания ${file.name}`);
  }

  const doc = await response.json();
  lastPredicted = doc;
  fillDoc(doc);
  $("status").textContent = `Готово: ${file.name}. Проверьте и при необходимости исправьте поля.`;
}

async function recognizeFirstPending() {
  if (!pendingFiles.length) {
    $("status").textContent = "Добавьте хотя бы один PDF в список";
    return;
  }
  const next = pendingFiles[0];
  await recognizeFile(next.file);
  removePendingFile(next.key);
  await refreshLists();
}

async function recognizeAllPending() {
  if (!pendingFiles.length) {
    $("status").textContent = "Добавьте хотя бы один PDF в список";
    return;
  }

  while (pendingFiles.length) {
    const next = pendingFiles[0];
    await recognizeFile(next.file);
    removePendingFile(next.key);
  }
  $("status").textContent = "Все прикреплённые документы распознаны";
  await refreshLists();
}

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

$("pdf-file").addEventListener("change", (e) => {
  addPendingFiles(e.target.files);
  e.target.value = "";
});

const dropzone = $("dropzone");
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("drag-over");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("drag-over");
});

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag-over");
  addPendingFiles(e.dataTransfer.files);
});

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await recognizeFirstPending();
  } catch (err) {
    $("status").textContent = err.message;
  }
});

$("recognize-all").addEventListener("click", async () => {
  try {
    await recognizeAllPending();
  } catch (err) {
    $("status").textContent = err.message;
  }
});

$("save-training").addEventListener("click", async () => {
  if (!lastPredicted) {
    alert("Сначала распознайте документ.");
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
renderPendingFiles();
refreshLists();
