"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { cloudoFetch } from "@/lib/api";
import {
  HiOutlinePlus,
  HiOutlinePencil,
  HiOutlineTrash,
  HiOutlineUsers,
  HiOutlineX,
} from "react-icons/hi";

interface Team {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  shared?: boolean;
}

export default function TeamsPage() {
  const router = useRouter();
  const [teams, setTeams] = useState<Team[]>([]);
  const [editing, setEditing] = useState<Team | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({
    id: "",
    name: "",
    description: "",
    enabled: true,
  });
  const [error, setError] = useState("");

  const loadTeams = async () => {
    const response = await cloudoFetch("/teams");
    if (response.ok) setTeams(await response.json());
  };

  useEffect(() => {
    const rawUser = localStorage.getItem("cloudo_user");
    try {
      if (JSON.parse(rawUser || "null")?.role !== "ADMIN") {
        router.push("/");
        return;
      }
    } catch {
      router.push("/login");
      return;
    }
    const fetchTeams = async () => {
      const response = await cloudoFetch("/teams");
      if (response.ok) setTeams(await response.json());
    };
    void fetchTeams();
  }, [router]);

  const startCreate = () => {
    setEditing(null);
    setModalOpen(true);
    setForm({ id: "", name: "", description: "", enabled: true });
    setError("");
  };

  const startEdit = (team: Team) => {
    setEditing(team);
    setModalOpen(true);
    setForm({
      id: team.id,
      name: team.name,
      description: team.description || "",
      enabled: team.enabled,
    });
    setError("");
  };

  const saveTeam = async (event: React.FormEvent) => {
    event.preventDefault();
    const response = await cloudoFetch("/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    if (!response.ok) {
      const data = await response.json();
      setError(data.error || "Unable to save team");
      return;
    }
    setEditing(null);
    setModalOpen(false);
    setForm({ id: "", name: "", description: "", enabled: true });
    await loadTeams();
  };

  const deleteTeam = async (team: Team) => {
    if (team.shared || !window.confirm(`Delete team ${team.name}?`)) return;
    const response = await cloudoFetch(
      `/teams?id=${encodeURIComponent(team.id)}`,
      {
        method: "DELETE",
      },
    );
    if (!response.ok) {
      const data = await response.json();
      setError(data.error || "Unable to delete team");
      return;
    }
    await loadTeams();
  };

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono">
      <div className="flex items-center justify-between px-8 py-5 border-b border-cloudo-border bg-cloudo-panel">
        <div className="flex items-center gap-4">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20">
            <HiOutlineUsers className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] uppercase">
              Teams
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em]">
              Administration // RESOURCE_SCOPE
            </p>
          </div>
        </div>
        <button
          onClick={startCreate}
          className="btn btn-primary h-10 px-4 flex items-center gap-2"
        >
          <HiOutlinePlus className="w-4 h-4" /> New Team
        </button>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-[1100px] mx-auto border border-cloudo-border bg-cloudo-panel overflow-hidden">
          {error && (
            <div className="px-6 py-4 border-b border-cloudo-err/30 text-cloudo-err text-xs uppercase tracking-widest">
              {error}
            </div>
          )}
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-cloudo-border bg-cloudo-accent/10">
                <th className="px-6 py-4 text-[10px] uppercase tracking-widest text-cloudo-muted">
                  Team
                </th>
                <th className="px-6 py-4 text-[10px] uppercase tracking-widest text-cloudo-muted">
                  Description
                </th>
                <th className="px-6 py-4 text-[10px] uppercase tracking-widest text-cloudo-muted">
                  Status
                </th>
                <th className="px-6 py-4 text-[10px] uppercase tracking-widest text-cloudo-muted text-right">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-cloudo-border/40">
              {teams.map((team) => (
                <tr key={team.id} className="hover:bg-cloudo-accent/[0.03]">
                  <td className="px-6 py-5 font-black uppercase tracking-widest text-sm">
                    {team.name}
                  </td>
                  <td className="px-6 py-5 text-sm text-cloudo-muted">
                    {team.description || "-"}
                  </td>
                  <td className="px-6 py-5 text-[10px] uppercase tracking-widest text-cloudo-accent">
                    {team.shared
                      ? "Shared"
                      : team.enabled
                        ? "Enabled"
                        : "Disabled"}
                  </td>
                  <td className="px-6 py-5 text-right">
                    {!team.shared && (
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => startEdit(team)}
                          className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent"
                          title="Edit team"
                        >
                          <HiOutlinePencil className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => deleteTeam(team)}
                          className="p-2 border border-cloudo-border text-cloudo-err hover:bg-cloudo-err hover:text-white"
                          title="Delete team"
                        >
                          <HiOutlineTrash className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {modalOpen && (
        <div
          className="fixed inset-0 z-50 bg-cloudo-dark/90 flex items-center justify-center p-4"
          onClick={() => setModalOpen(false)}
        >
          <form
            className="w-full max-w-md bg-cloudo-panel border border-cloudo-border p-6 space-y-5"
            onSubmit={saveTeam}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex justify-between items-center border-b border-cloudo-border pb-4">
              <h2 className="text-xs font-black uppercase tracking-[0.25em]">
                {editing ? "Edit Team" : "New Team"}
              </h2>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="p-1 text-cloudo-muted hover:text-cloudo-err"
              >
                <HiOutlineX />
              </button>
            </div>
            <label className="block text-[10px] uppercase tracking-widest text-cloudo-muted">
              Team ID
              <input
                required
                disabled={!!editing}
                value={form.id}
                onChange={(event) =>
                  setForm({ ...form, id: event.target.value })
                }
                className="input w-full h-11 mt-2"
                placeholder="platform"
              />
            </label>
            <label className="block text-[10px] uppercase tracking-widest text-cloudo-muted">
              Name
              <input
                required
                value={form.name}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
                className="input w-full h-11 mt-2"
                placeholder="Platform Team"
              />
            </label>
            <label className="block text-[10px] uppercase tracking-widest text-cloudo-muted">
              Description
              <textarea
                value={form.description}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
                className="input w-full min-h-24 mt-2"
              />
            </label>
            <label className="flex items-center gap-3 text-[10px] uppercase tracking-widest text-cloudo-muted">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) =>
                  setForm({ ...form, enabled: event.target.checked })
                }
              />{" "}
              Enabled
            </label>
            <button type="submit" className="btn btn-primary w-full h-11">
              Save Team
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
