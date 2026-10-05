const H1 = "text-[28px] leading-9 font-[650] sm:text-[32px] sm:leading-10";
const H2 = "text-xl font-semibold";

export function AboutPage() {
  return (
    <article className="max-w-prose space-y-8">
      <h1 className={H1}>About Pünktlich?</h1>

      <section className="space-y-2">
        <h2 className={H2}>What it is</h2>
        <p>
          An estimate of how likely a train is to leave <strong>6 or more minutes late</strong>, for
          about 30 large German stations, for the next few hours.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className={H2}>How it works</h2>
        <ol className="list-decimal space-y-1 pl-6">
          <li>
            We look only at the timetable: station, train type, line, destination, stop number, time
            of day, weekday and public holidays.
          </li>
          <li>
            A model trained on 9 months of real departures (December 2025 to August 2026) turns that
            into a probability.
          </li>
          <li>
            The probability is shown as a risk level: Low (under 20%), Medium (20 to 45%) or High
            (45% or more), always with the number.
          </li>
        </ol>
      </section>

      <section className="space-y-2">
        <h2 className={H2}>Limitations</h2>
        <ul className="list-disc space-y-1 pl-6">
          <li>These are estimates, not promises.</li>
          <li>The model does not know about today&apos;s disruptions, only the timetable.</li>
          <li>
            It was trained without autumn months, so October and November may be less accurate.
          </li>
          <li>
            In this local version the board shows real departures from a past day, replayed onto
            today.
          </li>
        </ul>
      </section>

      <section className="space-y-2">
        <h2 className={H2}>Data and credits</h2>
        <p>
          Timetable data: Deutsche Bahn Timetables API. History: piebro/deutsche-bahn-data (CC BY
          4.0). Pünktlich is an independent student project and is not affiliated with Deutsche
          Bahn.
        </p>
        <p>
          <a
            href="https://github.com/piyal21/Puenktlich-mlops-project"
            className="text-primary underline hover:text-primary-hover"
          >
            Source code on GitHub
          </a>
        </p>
      </section>
    </article>
  );
}
