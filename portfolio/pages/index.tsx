import Image from 'next/image';
import Link from 'next/link';

export default function Home() {
  return (
    <>
      <header>
        <h1>
          <Link href="/">Tyler Norlund</Link>
        </h1>
      </header>
      <div className="container">
        <main>
          <Image
            src="/face.png"
            alt="Tyler Norlund"
            width={200}
            height={200}
            style={{ borderRadius: '50%', objectFit: 'cover', marginTop: '1rem' }}
          />
          <Link href="/resume">
            <button>Résumé</button>
          </Link>
          <Link href="/receipt">
            <button>Receipt</button>
          </Link>
        </main>
      </div>
    </>
  );
}
