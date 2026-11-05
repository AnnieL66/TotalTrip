import Link from "next/link";
import { Header } from "@/components/Header";

export default function NotFound() {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-md p-10 text-center">
        <h1 className="font-serif text-2xl">We couldn&apos;t find that page</h1>
        <p className="mt-2 text-sm text-muted">It may have moved, or the link may be incomplete.</p>
        <Link href="/" className="mt-6 inline-block rounded-full bg-plum px-5 py-2 text-sm text-white">
          Plan a trip
        </Link>
      </main>
    </>
  );
}
