"use client";

import Link from "next/link";
import { HiOutlineArrowLeft, HiOutlineHome } from "react-icons/hi";

export default function NotFound() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-cloudo-dark text-cloudo-text">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(0,180,255,0.15),transparent_35%),radial-gradient(circle_at_80%_70%,rgba(0,180,255,0.12),transparent_30%)]" />

      <section className="relative z-10 mx-auto flex min-h-screen w-full max-w-5xl flex-col items-center justify-center gap-8 px-6 text-center">
        <div className="inline-flex items-center border border-cloudo-accent/40 bg-cloudo-accent/10 px-4 py-1 text-xs font-bold tracking-[0.25em] text-cloudo-accent uppercase">
          ClouDO • Not Found
        </div>

        <div className="space-y-4">
          <p className="text-7xl font-black tracking-[0.18em] text-cloudo-accent md:text-9xl">
            404
          </p>
          <h1 className="text-2xl font-black tracking-[0.08em] uppercase md:text-4xl">
            Endpoint non trovato
          </h1>
          <p className="mx-auto max-w-2xl text-sm text-cloudo-muted md:text-base">
            Sembra che la rotta richiesta non esista o sia stata spostata. Torna
            alla dashboard oppure rientra nello stream operativo precedente.
          </p>
        </div>

        <div className="flex flex-col items-center gap-3 sm:flex-row">
          <Link href="/" className="btn btn-primary min-w-52 justify-center">
            <HiOutlineHome className="text-base" />
            Vai alla dashboard
          </Link>
          <button
            onClick={() => window.history.back()}
            className="btn btn-ghost min-w-52 justify-center"
            type="button"
          >
            <HiOutlineArrowLeft className="text-base" />
            Torna indietro
          </button>
        </div>

        <div className="w-full max-w-3xl border border-cloudo-border bg-cloudo-panel/70 p-4 text-left backdrop-blur-sm">
          <p className="mb-2 text-[11px] tracking-[0.2em] text-cloudo-muted uppercase">
            Diagnostic output
          </p>
          <pre className="overflow-x-auto text-xs text-cloudo-muted">
            {`{\n  "status": 404,\n  "code": "ROUTE_NOT_FOUND",\n  "hint": "Check path or navigate from sidebar"\n}`}
          </pre>
        </div>
      </section>
    </main>
  );
}
