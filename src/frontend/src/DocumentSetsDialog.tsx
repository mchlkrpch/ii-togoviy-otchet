import { useEffect, useState } from "react";
import { request } from "./api";
import { Modal } from "./components";
import type { Job } from "./types";

export default function DocumentSetsDialog({ onClose, onImport }: {
  onClose: () => void;
  onImport: (job: Job) => void;
}) {
  const [sets, setSets] = useState<{ id: string; name: string; year: number }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    request<typeof sets>("/api/document-sets").then(setSets).catch(reason => setError(String(reason)));
  }, []);
  async function importSet(id: string) {
    setBusy(true);
    setError("");
    try {
      onImport(await request<Job>(`/api/document-sets/${id}/import`, { method: "POST" }));
    } catch (reason) {
      setError(String(reason));
      setBusy(false);
    }
  }
  return <Modal title="Наборы документов МФТИ" onClose={onClose}>
      <p>Выберите проверку и откройте редактор формулы → «Предсказать ИИ». Файлы и целевой показатель выбраны из описания набора. Формула и исходные ячейки определяются по метаданным документов.</p>
      <p>Источники этих наборов — опубликованные детализирующие финансовые таблицы.</p>
      {error && <p role="alert">{error}</p>}
      <div className="document-set-list">{sets.map(set => <button key={set.id} className="button secondary" disabled={busy} onClick={() => void importSet(set.id)}>
        {set.id} · {set.name}
      </button>)}</div>
      {!sets.length && !error && <p>Наборы загружаются. Для Docker подключите папку document_sets.</p>}
  </Modal>;
}
