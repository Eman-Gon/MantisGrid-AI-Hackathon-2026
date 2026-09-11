# MantisGrid AI Hackathon 2026 — Official Rules and Participant Agreement

> **DRAFT — NOT LEGAL ADVICE. NOT FOR PUBLICATION.**
>
> Assembled from the structures of the JPMorgan Chase *Data for Good* Official
> Rules, the MLH Contest Terms and Standard Hackathon Rules (CC BY-SA 4.0), and
> the Liquid AI hackathon terms. It needs a lawyer's pass before anyone sees it.
>
> **Everything marked `[DECIDE]` is a business decision, not a drafting gap.**
> There are 14. They are collected at the end.

---

## 1. About this event

MantisGrid AI (`[DECIDE: full legal entity name and registered address]`, the
**"Sponsor"**) is running a two-track hackathon on AI infrastructure reliability.

| Track | You build |
|---|---|
| **1 — Root cause analysis** | an agent that locates the root cause of real infrastructure failures, and the evaluation that shows how good it is |
| **2 — Cluster efficiency** | an analysis and dashboard identifying recoverable capacity in a GPU cluster |

Dates: `[DECIDE: start, end, submission deadline, timezone]`.
Format: `[DECIDE: in person / remote / hybrid, and venue]`.

By registering you accept these Rules and the decisions of the judges.

## 2. Definitions

**"Submission"** — everything you give us for judging: source code, the required
output artifacts, your report, your dashboard, and anything you present to judges.

**"Challenge Materials"** — everything we give you: the datasets, the API and its
documentation, the starter repositories, and any API keys we issue.

**"Feedback"** — comments, criticism, or design suggestions you make about
MantisGrid's products, APIs, or data model, whether in your Submission, in
conversation, or during judging. Section 11 covers this and you should read it.

## 3. Eligibility

Open to individuals aged 18 or over `[DECIDE: or 16+ with guardian consent]`.

You may not participate if you are an employee, contractor, or immediate family
member of an employee of the Sponsor, or if you are serving as an organizer,
judge, or mentor at this event.

`[DECIDE: geographic eligibility.]` This is not cosmetic — we issue API keys and
may pay prizes, both of which have sanctions and export-control implications.
Name the countries you will accept.

You are responsible for ensuring that participation does not breach any agreement
you have with your employer or your school. If you are employed, check whether
your employment agreement assigns work you do on your own time. **If it does, you
may not be able to grant the licence in Section 10 and you should not
participate.**

## 4. Registration and teams

Teams of `[DECIDE: minimum and maximum team size]`.
`[DECIDE: do participants form their own teams, or do we assign them by skill as
JPMorgan does?]`

Every member must register individually and accept these Rules. **If one member is
disqualified, the whole team is disqualified**, so choose teammates who will
follow the rules.

A team enters one track. `[DECIDE: may a team submit to both tracks?]`

## 5. Code of Conduct

All participants — and all sponsors, judges, mentors, volunteers and organizers —
must follow the MantisGrid Hackathon Code of Conduct, which is adapted from the
MLH Code of Conduct under CC BY-SA 4.0.

It applies at the venue, at related social events, in any transport we provide,
and in all online channels for the event.

Report a concern to `[DECIDE: named contact, email, and phone]`. We can remove
anyone from the event, without a refund of anything and without eligibility for
prizes.

## 6. Competition rules

1. **Do the work during the event.** You may arrive with ideas, designs, and a
   plan. You may not arrive with the code.
2. **You may use open-source libraries, frameworks, and public models.** Publishing
   your own pre-built project as open source in order to "reuse" it during the
   event is against the spirit of this rule and is not allowed.
3. **You may reuse an idea** you have worked on before, so long as you do not reuse
   the code.
4. **Stop when time is called.** Fixing a bug that breaks your demo is fine. Adding
   features is not.
5. **Every team member should participate.**
6. **Ask for help.** From organizers, mentors, and each other. This is encouraged,
   not tolerated.
7. **Do not submit the same work to another concurrent competition.**

## 7. AI tools

**This is an AI engineering event. You are expected to use AI tools heavily —
that is the point, not a concession.**

Two conditions:

1. **Disclose what you used.** Tools, models, and roughly how. Not to police you —
   we want to know, and the judging rubric rewards teams who can explain their
   choices.
2. **The engineering judgment must be yours.** You will be asked, at judging, why
   your system works the way it does. "The model wrote it" is not an answer, and
   the rubric weights explainability heavily on both tracks.

Track 1 additionally requires that your judged run use the model and API key we
issue. Details in the track brief.

## 8. The Challenge Materials, and the data in particular

**Read this section. It carries obligations that outlast the event.**

The datasets we provide are third-party research data licensed to us on terms we
do not control. Both are subject to `[DECIDE: final terms, pending responses from
MIT (mit-dcc@mit.edu) and the OpenRCA authors — Section 8 cannot be finalised
until these land]`.

You agree that you will:

- **Use the data only for this event.** Not for other research, not for other
  competitions, not for any commercial purpose, not to train or fine-tune models
  you keep afterwards `[DECIDE: confirm the fine-tuning prohibition — it is a real
  constraint on Track 1 teams and needs saying up front, not discovered later]`.
- **Not redistribute it.** Not publish it, mirror it, post it to a public
  repository, or upload it to a third-party service.
- **Delete it within `[DECIDE: 14/30] days** of the event ending, including copies
  in cloud storage, notebooks, and container images, and confirm deletion if we
  ask.
- **Not attempt to obtain, reconstruct, or use ground-truth answer keys** by any
  route other than what we provide. This includes downloading them from upstream
  sources, and it includes prompting a model to recall them.

On that last point, so nobody is surprised: **Track 1 submissions are scored twice,
on original and on transformed data, and the difference is published alongside
your accuracy.** The method is described in the track brief. A large difference is
not by itself an accusation — we will ask you about it before concluding anything.

The API, its documentation, and the starter repositories are provided for use
during this event only.

**Everything is provided "as is", with no warranty.** The synthetic portions of the
data are labelled as such in the briefs.

## 9. API keys and inference costs

We issue an API key to your team for the judged run.

- **Do not share it, publish it, or commit it.** Treat it as a credential.
- Use it for this event only.
- We can revoke it at any time, including for a usage pattern that looks like
  abuse.
- **We can see the usage on it.** Token consumption is a judged criterion on
  Track 1 and we read it from provider billing rather than from your report.

You are responsible for the cost of any other inference you choose to buy
`[DECIDE: or state the exploration budget we provide]`.

## 10. Your intellectual property

**You keep it.** You and your team own your Submission, subject only to the licence
below.

`[DECIDE: this is the recommended position and the biggest single decision in this
document. The alternative — JPMorgan's — is that all entries become the exclusive
property of the Sponsor. For a recruiting event we get everything we need from a
licence, and taking ownership of candidates' work is a bad look for a company
trying to hire them.]`

You grant the Sponsor a **non-exclusive, worldwide, royalty-free, perpetual,
irrevocable licence** to use, reproduce, adapt, and display your Submission in
order to:

- run and judge this event;
- promote this and future events, including screenshots and excerpts;
- inform our own product development.

We are not obliged to use it, credit you `[DECIDE: or should we commit to
crediting? It costs nothing and is the better look]`, or keep it confidential
unless we agree otherwise in writing.

**You warrant** that the Submission is your own work, that you have the right to
grant this licence, and that it does not infringe anyone else's rights. If it
incorporates third-party material, you must have the right to use it and must say
so.

`[DECIDE: do we require submissions to be public — a git repository — as MLH does?
It suits the open-source spirit and helps participants show their work, but it
interacts with Section 12: a Track 2 submission necessarily reveals the shape of
our API.]`

## 11. Feedback

**We ask you to criticise our design, and we intend to act on what you say.**
Track 2's brief says so directly: part of what we want from this event is finding
out where our proposed business API is wrong.

So, plainly: any Feedback you give us is **not confidential**, and we may use it
without restriction, without compensation, and without obligation to you. You do
not lose any right to use your own ideas yourself.

We would rather write this down than take your good ideas by silence.

## 12. Confidentiality

You will receive material about MantisGrid's products that is not public,
including the production API contract described in the Track 2 brief.

You agree to keep confidential anything we mark or identify as confidential, and
not to disclose it outside your team `[DECIDE: for how long — 1/2/3 years?]`.

This does not cover anything that is already public, that you already knew, or
that you work out independently.

`[DECIDE: is the Layer A API contract actually confidential, or are we content for
it to be effectively public once several teams have it? Choose now. A
confidentiality clause nobody intends to enforce is worse than none.]`

## 13. Judging

Judged against the published rubric (`JUDGING.md`), which is available before the
event and is scored as written.

- Each submission is scored independently by at least two judges, who then
  reconcile.
- A judge with a conflict of interest — including having interviewed or worked
  with a participant — must recuse from that submission, and a substitute is
  assigned.
- Judges may inspect and run submissions at any point during judging.
- Your team must be available during the judging window to demonstrate your work.
  A submission that will not run may be scored on what can be observed
  `[DECIDE: how long do we spend trying to fix a broken submission before scoring
  it as-is? Agree the timebox now, not on the day]`.

**`[DECIDE: appeals. Two clean options.]` (a) *JPMorgan:* the judges' decisions are
final and no correspondence will be entered into. (b) A short written appeal
window on process errors only, not on scoring judgment. Pick one and state it
here.**

## 14. Prizes

`[DECIDE: prize structure, per track and per placing. This changes participant
behaviour more than anything else in this document — a single accuracy prize makes
the rest of the rubric decorative.]`

- All prizes are awarded **"as is"** with no warranty. We may substitute a prize of
  equal or greater value.
- Limit one prize per person.
- **Winners are responsible for their own taxes.** In the US the value may be
  reportable to the IRS on Form 1099-MISC, and we may need a W-9 before paying.
- For a team prize, the team is responsible for splitting it. We are not.
- We may require a signed eligibility and release form within `[DECIDE: 7] days`
  before paying, and may select an alternate winner if we do not receive it.

## 15. Recruiting

We are running this event partly to meet people we might want to hire. Being
straight about what that means:

**Participation does not create an employment relationship, and is not an offer or
promise of employment.**

`[DECIDE: and then say what it actually is. The honest options: "we may contact
strong participants about open roles" / "finalists are guaranteed a first-round
interview" / "this has no formal relationship to our hiring process". Any of these
is fine. Silence is not — people will assume the most favourable one.]`

`[DECIDE: may we retain your submission and contact details for recruiting
purposes, and for how long? This is a GDPR question if we admit EU participants.]`

## 16. Publicity

By participating you consent to our use of your name, likeness, photographs, video,
and statements you make, for promotion of this and future events, without further
compensation, except where prohibited by law.

`[DECIDE: opt-out. A "please do not photograph me" mechanism is standard at
well-run events and costs nothing. Recommended.]`

## 17. Privacy and data retention

We collect registration details to run the event and to administer prizes.

- `[DECIDE: what we retain of your Submission and contact details, and for how
  long]`
- `[DECIDE: third-party platforms used — Devpost, Discord, Slack — each needs
  naming, as participants' data goes there]`
- We are not responsible for data you choose to put into third-party tools.

If we admit participants from the EU or UK, this section needs a GDPR review and a
lawful basis for each processing purpose.

## 18. Disqualification

We may disqualify a team, at our discretion, for breaching these Rules or the Code
of Conduct, for obtaining or using answer keys outside the sanctioned route, for
misrepresenting authorship, or for other unsporting behaviour.

We will tell you why.

## 19. Liability

You agree to release the Sponsor and its officers, employees, agents, and partners
from any claim arising from your participation, from acceptance or use of a prize,
or from your use of any third-party platform used during the event.

To the fullest extent permitted by law, the Sponsor is not liable for indirect,
incidental, special, or consequential damages, including loss of profit or
business opportunity.

We may cancel, suspend, or modify the event if its integrity is compromised or if
something outside our reasonable control prevents us running it.

## 20. General

These Rules are governed by the laws of `[DECIDE: jurisdiction — depends on where
MantisGrid AI is incorporated and where the event is held]`, and disputes go to the
courts of that jurisdiction.

If we do not enforce a term, we have not waived it. If a term is unenforceable, the
rest stands.

`[DECIDE: if we accept participants outside that jurisdiction, decide whether one
governing law is workable — JPMorgan uses a per-country table for exactly this
reason.]`

---

## Registration acknowledgements

Required checkboxes at registration, following the MLH pattern:

☐ **Required.** I have read and agree to the MantisGrid Hackathon Official Rules
and Participant Agreement.

☐ **Required.** I have read and agree to the MantisGrid Hackathon Code of Conduct.

☐ **Required.** I understand that the datasets provided are third-party research
data, that I may use them only for this event, and that I must delete them
afterwards.

☐ **Required.** I confirm that my participation does not breach any agreement with
my employer or school.

☐ Optional. I would like MantisGrid to contact me about career opportunities.

☐ Optional. I consent to being photographed and filmed at the event.

---

## The 14 decisions

Nothing here is a drafting problem. Each needs an answer from MantisGrid.

| # | § | Decision | Notes |
|---|---|---|---|
| 1 | 1 | Legal entity, address, dates, format | mechanical, but blocks everything |
| 2 | 3 | Minimum age; geographic eligibility | affects sanctions and export control on API keys |
| 3 | 4 | Team size; self-formed or assigned; one track or both | the teaser deck already asks people to think about teams |
| 4 | 5 | Code of Conduct contact — name, email, phone | a policy with no contact is not a policy |
| 5 | 8 | **Final data terms** | blocked on MIT and the OpenRCA authors. Gates announcing |
| 6 | 8 | Is fine-tuning on the data prohibited? | a real constraint on Track 1; say it up front |
| 7 | 9 | Do teams get an exploration budget on our key? | otherwise "unconstrained ablations" only applies to funded teams |
| 8 | 10 | **Licence or ownership** | recommended: licence. Biggest decision here |
| 9 | 10 | Must submissions be public? | interacts with §12 |
| 10 | 12 | Is the Layer A contract actually confidential? | a clause we will not enforce is worse than none |
| 11 | 13 | Appeals: final, or a narrow process-only window | and the timebox for a submission that will not run |
| 12 | 14 | Prize structure | changes behaviour more than anything else in this document |
| 13 | 15 | **What participation means for hiring** | silence makes people assume the most favourable answer |
| 14 | 20 | Governing law, and whether one is enough | JPMorgan uses a per-country table |

**5, 8, and 13 are the ones that need real thought.** The rest are mostly a
half-hour and a decision-maker.
