# Things I need from you

Last updated: 2026-08-15. I will keep this list short and remove items when they
are settled.

## Question to answer now

1. After reviewing the [MiniMax Music 3 license](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE),
   are you okay with a local-only install and benchmark on this computer? I will
   show the required `MiniMax-Music3` credit. LAN and Cloudflare access will stay
   off for now.

There is no Hugging Face button to click for Music 3; its repository is public.

## I will ask later, if needed

- H3 needs a separate written MiniMax license to run in the United States.
  Accepting ordinary Hugging Face terms is not enough.
- Krea 2's license requirements conflict with Maestro's no-moderation rule. I
  will only bring this back when there is a clear choice to make.
- Music and Character Sheet quality checks can wait until there are real outputs
  to review.

## Already decided

- Music 3 is required.
- Character Sheets start from a FLUX anchor. Quad FLUX is the safe default;
  Krea choices are explicit, and Dynamic Krea stays experimental. A local VLM
  and Qwen Image Edit handle review and repair.
- Maestro gets first call on the 5090 for development and benchmarks, but will
  coordinate with both Palimpsest tasks and will not hog it. Samples come last.
- Direct compute stays locked until verified development-cost recovery reaches
  $1,000.
- The only owner account cannot be disabled.

## Nothing for you to do

- Cloudflare is deployed and Wrangler is logged out.
- Existing projects are already connected to the owner account.
- Beads is working. Do not initialize or migrate it.
