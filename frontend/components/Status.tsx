import Link from "next/link";
import { Header } from "./Header";

export function Loading() {
  return (
    <>
      <Header />
      <p className="p-10 text-center text-muted">Loading your trip…</p>
    </>
  );
}

export function Failure({ message }: { message: string }) {
  return (
    <>
      <Header />
      <div className="mx-auto max-w-md p-10 text-center">
        <p className="font-serif text-2xl">We couldn&apos;t open this trip</p>
        <p className="mt-2 text-sm text-muted">{message}</p>
        <Link href="/" className="mt-6 inline-block rounded-full bg-plum px-5 py-2 text-sm text-white">
          Start a new trip
        </Link>
      </div>
    </>
  );
}
