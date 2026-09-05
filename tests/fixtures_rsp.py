"""A verbatim slice of Wikipedia:Reliable sources/Perennial sources wikitext.

Kept as a fixture so the table parser is tested without hitting the network,
and so a change to Wikipedia's markup shows up as a failing test rather than as
a silently empty knowledge base.
"""

RSP_WIKITEXT = """{{WP:RSPTableFull}}
{| class="wikitable sortable perennial-sources"
|+ Perennial sources
!rowspan="2" scope="col" | Source
<onlyinclude>
|- class="s-gu" id="112 Ukraine"
| [[112 Ukraine]]
| {{WP:RSPSTATUS|gu}}
| {{rsnl|281|RfC: Deprecation of fake news|2019|rfc=y}} [[WP:X#news|1]]<br> [[WP:Y|A]]
| {{WP:RSPLAST|2020|stale=n}}
| 112 Ukraine was deprecated following a 2019 RfC, which showed overwhelming \
consensus for the deprecation of a slew of sources associated with Russian \
disinformation in Ukraine.
| {{WP:RSPUSES|112.ua|112.international}}
|- class="s-gr" id="BBC"
| [[BBC]]
| {{WP:RSPSTATUS|gr}}
| [[WP:Archive 318#BBC|1]] [[WP:Archive 346#Some reliable sources|2]]
| {{WP:RSPLAST|2021}}
| The BBC is a British publicly funded broadcaster. It is considered generally \
reliable, including BBC News and BBC documentaries.
| {{WP:RSPUSES|bbc.com|bbc.co.uk}}
|- class="s-d" id="Daily Mail"
| [[Daily Mail]]
| {{WP:RSPSTATUS|d}}
| [[WP:Archive 220#Daily Mail RfC|1]]
| {{WP:RSPLAST|2019}}
| In the 2017 RfC, the Daily Mail was the first source to be deprecated on \
Wikipedia, and its use as a reference is generally prohibited.
| {{WP:RSPUSES|dailymail.co.uk|mailonline.co.uk}}
|- class="s-nc" id="Vice"
| [[Vice (magazine)|Vice]]
| {{WP:RSPSTATUS|nc}}
| [[WP:Archive 300#Vice|1]]
| {{WP:RSPLAST|2022}}
| There is no consensus on the reliability of Vice; context matters.
| {{WP:RSPUSES|vice.com}}
</onlyinclude>
|}
"""

# The rules array from User:Headbomb/unreliable.js, trimmed to three entries.
HEADBOMB_JS = r"""
// Instructions available at [[User:Headbomb/unreliable]]
var rules = [
        {
            comment: 'Predatory journal or publisher',
            regex: /\b(?:omicsonline\.org|scirp\.org)/i,
            css: { "background-color": "#ffbbbb" },
            important: true
        },
        {
            comment: 'Preprint or general repository which may or may not contain unpublished, self-published, or predatory materials',
            regex: /\b(?:academia\.edu|researchgate\.net|arxiv\.org)/i,
            css: { "background-color": "#fffdd0" },
            important: true
        },
        {
            comment: 'Predatory journal or publisher',
            regex: /\b10\.(?:4172|19080)\b/,
            css:  { "background-color": "#ffbbbb" },
            important: true
        },
];
"""
