import { useState } from "react";
import { HiOutlineTrash } from "react-icons/hi";
import { cloudoFetch } from "@/lib/api";

interface Schema {
  PartitionKey: string;
  RowKey: string;
  id: string;
  name: string;
  description: string;
  runbook: string;
  run_args: string;
  worker: string;
  oncall: string;
  require_approval: boolean;
  severity?: string;
  monitor_condition?: string;
  tags?: string;
}

interface Schedule {
  id: string;
  name: string;
  cron: string;
  runbook: string;
  run_args: string;
  queue?: string;
  worker_pool?: string;
  enabled: boolean;
  last_run?: string;
}

export function DeleteConfirmationModal({
  schema,
  type,
  onClose,
  onSuccess,
  onError,
}: {
  schema: Schema | Schedule | { id: string };
  type: "schemas" | "schedules" | "users";
  onClose: () => void;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [isDeleting, setIsDeleting] = useState(false);
  const isUser = type === "users";

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      const query = isUser ? "username" : "id";
      const response = await cloudoFetch(
        `/${type}?${query}=${encodeURIComponent(schema.id)}`,
        { method: "DELETE" },
      );
      const data = await response.json();

      if (!response.ok) {
        onError(
          data.error || (isUser ? "Failed to revoke access" : "Delete failed"),
        );
        setIsDeleting(false);
        return;
      }

      onSuccess(
        isUser
          ? `Access revoked for ${schema.id}`
          : `Entry "${schema.id}" destroyed`,
      );
      onClose();
    } catch {
      onError("Network error // destruction failed");
      setIsDeleting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-cloudo-dark/95 backdrop-blur-md flex items-center justify-center z-[60] p-4">
      <div className="bg-cloudo-panel border border-cloudo-err/30 max-w-sm w-full p-10 text-center space-y-8 animate-in zoom-in-95 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-full h-1 bg-cloudo-err/50" />

        <div className="w-14 h-14 bg-cloudo-err/10 border border-cloudo-err/20 flex items-center justify-center mx-auto text-cloudo-err">
          <HiOutlineTrash className="w-7 h-7" />
        </div>

        <div className="space-y-3">
          <h3 className="text-[11px] font-black text-cloudo-text uppercase tracking-[0.3em]">
            Destructive Action
          </h3>
          <p className="text-[9px] text-cloudo-muted uppercase font-bold leading-relaxed">
            {isUser
              ? "Permanently revoke access for user:"
              : `Permanently delete ${type} entry:`}
            <br />
            <span className="text-cloudo-err mt-2 block font-mono">
              {schema.id}
            </span>
          </p>
        </div>

        <div className="flex flex-col gap-3 pt-4">
          <button
            onClick={handleDelete}
            disabled={isDeleting}
            className="w-full bg-cloudo-err text-cloudo-text py-4 text-[10px] font-black uppercase tracking-[0.3em] hover:bg-cloudo-err/90 transition-all disabled:opacity-50"
          >
            {isDeleting ? "Destroying..." : "Confirm Destruction"}
          </button>
          <button
            onClick={onClose}
            disabled={isDeleting}
            className="text-[9px] font-black text-cloudo-muted hover:text-cloudo-text uppercase tracking-widest py-2 transition-all"
          >
            Cancel Action
          </button>
        </div>
      </div>
    </div>
  );
}
