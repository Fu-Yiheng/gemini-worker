# Common AI-sounding phrases (generic list)

Avoid the words and patterns below when polishing. After polishing, the script runs the two regular expressions at the end of this file over the original and the polished text and lists the hits that are **new** in the polished text.

This is a generic list. To use your own, set `rules` (rules attached for Gemini) and `tells` (regexes for the check, in the same format as the two code blocks at the end of this file) in `~/.gemini-worker/config.json`; see the README.

## English

- Inflated words: delve, crucial, pivotal, paramount, intricate, multifaceted, holistic, seamless, meticulous, realm, tapestry, testament
- Puffed-up verbs: underscore, showcase, leverage, harness, foster, unveil, elucidate, shed light on, pave the way, bridge the gap
- Filler connectives: moreover, furthermore, additionally, notably, importantly, interestingly; "in conclusion" and "in summary" at the end of a paragraph
- Stock phrases: plays a crucial / pivotal / vital role; it is worth noting that; it should be noted that; with the rapid development of; has attracted increasing attention
- Trailing participle commentary: "…, highlighting the importance of …", "…, demonstrating that …"; summary sentences starting with "This highlights / underscores / demonstrates"
- Stacked hedges: may potentially, could possibly, seems to suggest

## Chinese

- Stock openings: 随着……的快速发展；……受到越来越多的关注；……是研究热点
- Signposting: 值得注意的是、需要指出的是、不难发现、由此可见、综上所述、总而言之、总的来说
- Empty verbs and verdicts: 深入探讨、全面分析、赋能、助力、起到了……作用、具有重要意义、提供了参考
- Piled-up intensifiers: 至关重要、不可或缺、显著提升、有效提高
- Stacked hedges: 在一定程度上可能、或许可能

## Regular expressions (used by the script)

EN (case-insensitive):
```
\b(delv\w*|crucial|pivotal|paramount|intricat\w*|multifaceted|holistic|seamless\w*|meticulous\w*|realm|tapestry|testament|underscor\w*|showcas\w*|leverag\w*|harness\w*|foster\w*|unveil\w*|elucidat\w*|moreover|furthermore|additionally|notably|importantly|interestingly)\b|shed\w* light on|pav\w* the way|bridg\w* the gap|plays? an? (crucial|pivotal|vital|key|significant|important) role|\bit is (worth|important to) not(e|ing)\b|\bit should be noted\b|\bwith the (rapid|continuous|ongoing) development of\b|\b(attracted|received|gained) (increasing|growing|considerable|significant) attention\b|, (highlighting|underscoring|demonstrating|showcasing|emphasizing) (the|its|their|that|how)\b|\bthis (highlights|underscores|demonstrates|showcases)\b|\b(may|might|could) (possibly|potentially)\b|\b(seems?|appears?) to suggest\b|\bin (conclusion|summary)\b
```

ZH:
```
随着.{0,30}(快速|迅速|不断|飞速|蓬勃)发展|受到.{0,6}(广泛|越来越多|日益)的?关注|研究热点|值得(注意|一提|关注)的是|需要(指出|注意|说明|强调)的是|不难(发现|看出)|由此可见|综上所述|总而言之|总的来说|深入(探讨|分析|研究|剖析)|全面(分析|研究|探讨)|赋能|助力|起到了?.{0,10}作用|具有(重要|重大|深远).{0,8}(意义|价值)|提供了?.{0,6}(参考|借鉴|思路)|至关重要|不可或缺|显著(提升|提高|改善|增强)|有效地?(提升|提高|改善|解决)|在?一定程度上.{0,4}(可能|或许)|(或许|也许)可能|可能(或许|也许)
```
