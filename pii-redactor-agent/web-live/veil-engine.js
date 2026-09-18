/*!
 * VEIL engine — browser port.
 *
 * This mirrors veil/detectors.py, veil/actions.py and veil/scanner.py closely
 * enough that the demo can be driven entirely in the page with no network. It is
 * a *port*, so it can drift; tools/parity_check.py runs both engines over the
 * same inputs and fails when any field differs. If you change a rule here,
 * expect that check to tell you.
 *
 * Browser constraints that shaped the code:
 *   - No `require`, no build step: one file, one global, loaded with <script>.
 *   - No lookbehind-dependent behaviour beyond what every engine supports, so
 *     the regexes avoid `(?<...)` except where a boundary is truly needed.
 *   - The demo data is embedded by tools/build_web_data.py rather than fetched,
 *     because a page that fetches its own data is not static and breaks behind a
 *     strict CSP.
 */
(function (root) {
  "use strict";

  var MASK_CHAR = "\u2022";
  var HASH_LENGTH = 12;
  var MIN_PARTIAL_LENGTH = 8;

  var RISK_WEIGHTS = {
    SECRET: 10, CREDIT_CARD: 9, SSN: 9, IBAN: 8, NATIONAL_ID: 8,
    DATE_OF_BIRTH: 5, EMAIL: 4, PHONE: 4, PERSON: 3, MAC: 3,
    IPV4: 2, IPV6: 2, URL: 2
  };

  var ACTIONS = ["mask", "redact", "hash", "tokenize", "remove", "keep"];

  var DEFAULT_ACTIONS = {
    SECRET: "remove", CREDIT_CARD: "mask", SSN: "mask", IBAN: "mask",
    NATIONAL_ID: "mask", EMAIL: "mask", PHONE: "mask", DATE_OF_BIRTH: "mask",
    IPV4: "mask", IPV6: "mask", MAC: "mask", URL: "redact", PERSON: "redact"
  };

  var ALL_ENTITIES = [
    "EMAIL", "PHONE", "CREDIT_CARD", "IBAN", "SSN", "NATIONAL_ID", "IPV4",
    "IPV6", "MAC", "URL", "SECRET", "DATE_OF_BIRTH", "PERSON"
  ];

  var DEFAULT_ENTITIES = ALL_ENTITIES.filter(function (e) { return e !== "PERSON"; });

  var RISK_BANDS = [[44, "CRITICAL"], [9, "HIGH"], [3, "MODERATE"], [1, "LOW"], [0, "NONE"]];

  var OPT_IN = ["PERSON"];

  /* ---------------------------------------------------------------- utils */

  function digitsOnly(value) { return value.replace(/\D/g, ""); }

  function uniquePush(list, value) {
    if (list.indexOf(value) === -1) { list.push(value); }
    return list;
  }

  /* ----------------------------------------------------------- validators */

  function luhnValid(digits) {
    if (!digits || !/^\d+$/.test(digits)) { return false; }
    var total = 0, index, value;
    for (index = 0; index < digits.length; index += 1) {
      value = parseInt(digits.charAt(digits.length - 1 - index), 10);
      if (index % 2 === 1) {
        value *= 2;
        if (value > 9) { value -= 9; }
      }
      total += value;
    }
    return total % 10 === 0;
  }

  function ibanValid(raw) {
    var compact = raw.replace(/[\s\-]/g, "").toUpperCase();
    if (compact.length < 15 || compact.length > 34) { return false; }
    if (!/^[A-Z]{2}\d{2}[A-Z0-9]+$/.test(compact)) { return false; }
    var rearranged = compact.slice(4) + compact.slice(0, 4);
    var remainder = 0, i, ch, chunk;
    for (i = 0; i < rearranged.length; i += 1) {
      ch = rearranged.charAt(i);
      chunk = /\d/.test(ch) ? ch : String(ch.charCodeAt(0) - 55);
      remainder = (remainder * (chunk.length === 2 ? 100 : 10) + parseInt(chunk, 10)) % 97;
    }
    return remainder === 1;
  }

  function ssnValid(digits) {
    if (!/^\d{9}$/.test(digits)) { return false; }
    var area = digits.slice(0, 3), group = digits.slice(3, 5), serial = digits.slice(5);
    if (area === "000" || area === "666") { return false; }
    if (parseInt(area, 10) >= 900) { return false; }
    if (group === "00" || serial === "0000") { return false; }
    if (digits === "123456789" || digits === "111111111") { return false; }
    return true;
  }

  function ipv4Valid(octets) {
    if (octets.length !== 4) { return false; }
    for (var i = 0; i < octets.length; i += 1) {
      var o = octets[i];
      if (!/^\d+$/.test(o)) { return false; }
      if (o.length > 1 && o.charAt(0) === "0") { return false; }
      var n = parseInt(o, 10);
      if (n < 0 || n > 255) { return false; }
    }
    return true;
  }

  function emailValid(value) {
    if (value.split("@").length !== 2) { return false; }
    var parts = value.split("@");
    var local = parts[0], domain = parts[1];
    if (!local || local.length > 64) { return false; }
    if (/^\./.test(local) || /\.$/.test(local) || local.indexOf("..") !== -1) { return false; }
    if (/^\./.test(domain) || /\.$/.test(domain) || domain.indexOf("..") !== -1) { return false; }
    var labels = domain.split(".");
    if (labels.length < 2) { return false; }
    for (var i = 0; i < labels.length; i += 1) {
      var label = labels[i];
      if (!label || label.length > 63) { return false; }
      if (/^-/.test(label) || /-$/.test(label)) { return false; }
    }
    return /^[A-Za-z]{2,24}$/.test(labels[labels.length - 1]);
  }

  function isIpv4Shape(value) { return /^\d{1,3}(\.\d{1,3}){3}$/.test(value); }

  function isDateShape(value) {
    var parts = value.split(/[-/.]/);
    if (parts.length !== 3) { return false; }
    for (var i = 0; i < parts.length; i += 1) {
      if (!/^\d+$/.test(parts[i])) { return false; }
    }
    var lengths = parts.map(function (p) { return p.length; }).join(",");
    var allowed = ["4,2,2", "2,2,4", "1,2,4", "2,1,4", "4,1,2", "4,2,1"];
    if (allowed.indexOf(lengths) === -1) { return false; }
    var nums = parts.map(Number), year, month, day;
    if (parts[0].length === 4) { year = nums[0]; month = nums[1]; day = nums[2]; }
    else if (parts[2].length === 4) { year = nums[2]; month = nums[0]; day = nums[1]; }
    else { year = nums[0]; month = nums[1]; day = nums[2]; }
    return year >= 1900 && year <= 2100 && month >= 1 && month <= 12 &&
           day >= 1 && day <= 31;
  }

  function validDateParts(year, month, day) {
    return year >= 1900 && year <= 2026 && month >= 1 && month <= 12 &&
           day >= 1 && day <= 31;
  }

  /* ------------------------------------------------------------- patterns */

  var PHONE_CONTEXT = ["phone", "telephone", "tel", "mobile", "cell", "cellphone",
    "whatsapp", "call", "fax", "contact", "hotline", "dial", "sms", "number"];

  var BIRTH_CONTEXT = ["dob", "d.o.b", "date of birth", "birthdate", "birth day",
    "birthday", "born", "yob", "birth", "naissance", "geburtsdatum"];

  var PLACEHOLDERS = ["changeme", "change_me", "change-me", "your_api_key",
    "yourapikey", "your-key-here", "your_token_here", "your-password",
    "your_password", "replace_me", "replaceme", "replace-me", "replace",
    "insert_key_here", "xxx", "xxxx", "todo", "none", "null", "true", "false",
    "example", "password", "secret", "token", "undefined", "placeholder",
    "dummy", "redacted", "removed", "string", "value", "here", "notset",
    "unset", "n/a", "na", "empty"];

  var FIRST_NAMES = ["aaron", "adam", "adrian", "ahmed", "aisha", "alex",
    "alexander", "alice", "ali", "amanda", "amelia", "amy", "andrew", "angela",
    "anna", "anthony", "arthur", "ashley", "austin", "benjamin", "beverly",
    "bilal", "brandon", "brenda", "brian", "bruce", "caleb", "cameron", "carla",
    "carlos", "carmen", "carol", "catherine", "charles", "charlotte", "cheryl",
    "chloe", "christina", "christopher", "claire", "clara", "colin", "connor",
    "craig", "cynthia", "daniel", "danielle", "david", "dawn", "deborah",
    "denise", "dennis", "derek", "diana", "diego", "donald", "donna", "dorothy",
    "douglas", "duncan", "edward", "elena", "elizabeth", "emily", "emma",
    "eric", "erica", "ethan", "eugene", "evelyn", "fatima", "felix", "fiona",
    "frances", "frank", "gabriel", "gareth", "gary", "george", "gerald", "gina",
    "gloria", "grace", "graham", "gregory", "hannah", "harold", "hassan",
    "hayley", "heather", "helen", "henry", "holly", "hugo", "ian", "ibrahim",
    "irene", "isaac", "isabel", "ivan", "jack", "jacob", "james", "jamie",
    "janet", "jason", "javier", "jeffrey", "jennifer", "jeremy", "jessica",
    "joan", "joel", "john", "jonathan", "jordan", "jose", "joseph", "joshua",
    "joyce", "julia", "julian", "julie", "justin", "karen", "katherine",
    "kathleen", "katie", "keith", "kelly", "kenneth", "kevin", "kimberly",
    "kiran", "larry", "laura", "lauren", "lawrence", "leah", "leonard",
    "leslie", "liam", "lily", "linda", "lisa", "logan", "lorenzo", "louis",
    "lucas", "lucy", "lydia", "madison", "margaret", "maria", "marie",
    "marilyn", "mark", "martha", "martin", "mary", "matthew", "maureen",
    "megan", "melissa", "michael", "michelle", "miguel", "mohamed", "mohammed",
    "molly", "monica", "muhammad", "mustafa", "nadia", "nancy", "naomi",
    "natalie", "nathan", "neil", "nicholas", "nicole", "nigel", "noah", "nora",
    "oliver", "olivia", "omar", "oscar", "owen", "pamela", "patricia",
    "patrick", "paul", "paula", "peter", "philip", "phillip", "rachel", "ralph",
    "raymond", "rebecca", "rehan", "richard", "robert", "roberto", "roger",
    "roland", "ronald", "rosa", "rose", "ross", "ruby", "russell", "ruth",
    "ryan", "saeed", "sally", "samuel", "sandra", "sara", "sarah", "scott",
    "sean", "simon", "sofia", "sonia", "sophia", "stanley", "stella",
    "stephen", "steven", "stuart", "susan", "sylvia", "tanya", "teresa",
    "terry", "theodore", "theresa", "thomas", "tiffany", "timothy", "tina",
    "tobias", "tom", "tony", "tracy", "travis", "trevor", "tyler", "usman",
    "valerie", "vanessa", "vera", "victor", "victoria", "vincent", "virginia",
    "walter", "warren", "wayne", "wendy", "wesley", "william", "willow",
    "yusuf", "zachary", "zainab", "zara", "zoe"];

  var MONTHS = ["january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"];

  var SECRET_PATTERNS = [
    ["aws_access_key", /(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}/g],
    ["github_token", /(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})/g],
    ["slack_token", /xox[abprs]-[A-Za-z0-9-]{10,}/g],
    ["stripe_key", /(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}/g],
    ["jwt", /eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}/g],
    ["pem_private_key", /-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )*PRIVATE KEY-----/g],
    ["bearer_token", /Bearer[ \t]+([A-Za-z0-9_\-\.]{16,})/g]
  ];

  var SECRET_ASSIGN_RE = /(?:api[_-]?key|apikey|api[_-]?secret|access[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token|access[_-]?token|refresh[_-]?token|bearer[_-]?token|private[_-]?key|password|passwd|pwd|secret|token)[ \t]*[:=][ \t]*["']?([^\s"'`,;<>{}$]{6,})["']?/gi;

  var EMAIL_RE = /[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}/g;
  // The lookbehind/lookahead are load-bearing, not decoration: without them the
  // permissive candidate scan starts *inside* a longer run of digits, so a
  // 22-digit value yields a 13-19 digit window that can satisfy Luhn purely by
  // accident. The parity harness caught exactly that on the AWS placeholder key
  // (`AKIASYNTHETICKEY0000` + a trailing digit run) producing a phantom
  // `000000000000000000` card that the Python engine never reports.
  var CARD_RE = /(?<![\w])(?:\d[ \-]?){12,18}\d(?![\w])/g;
  var IBAN_RE = /[A-Z]{2}\d{2}(?: ?[A-Z0-9]{2,4}){2,8}/g;
  var SSN_RE = /(\d{3})([ \-])(\d{2})\2(\d{4})/g;
  var NATIONAL_ID_RE = /\d{5}-\d{7}-\d/g;
  var IPV4_RE = /(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/g;
  var MAC_RE = /(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}/g;
  var URL_RE = /https?:\/\/[^\s<>"'`\)\]\}]+/g;
  var PHONE_RE = /\+?\(?\d[ \t\d().\-]{5,22}\d/g;
  var DOB_ISO_RE = /\b(\d{4})-(\d{2})-(\d{2})\b/g;
  var DOB_SLASH_RE = /\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b/g;
  var HONORIFIC_RE = /\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Sir|Dame|Rev|Capt|Sgt)\.?[ \t]+[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2}/g;

  /* ------------------------------------------------------------ detectors */

  function matchAll(text, regex) {
    var out = [], match;
    regex.lastIndex = 0;
    while ((match = regex.exec(text)) !== null) {
      out.push(match);
      if (match.index === regex.lastIndex) { regex.lastIndex += 1; }
    }
    return out;
  }

  function span(start, end, entity, value, detector, confidence, note) {
    return {
      start: start, end: end, entity: entity, value: value,
      detector: detector, confidence: confidence, note: note || ""
    };
  }

  function detectSecrets(text) {
    var spans = [], i, m;
    for (i = 0; i < SECRET_PATTERNS.length; i += 1) {
      var name = SECRET_PATTERNS[i][0], regex = SECRET_PATTERNS[i][1];
      matchAll(text, regex).forEach(function (match) {
        var start, end, value;
        if (name === "bearer_token") {
          start = match.index + match[0].lastIndexOf(match[1]);
          end = start + match[1].length;
          value = match[1];
        } else {
          start = match.index;
          value = match[0];
          end = start + value.length;
        }
        spans.push(span(start, end, "SECRET", value, name, 0.99,
          "matched a known provider credential format"));
      });
    }
    matchAll(text, SECRET_ASSIGN_RE).forEach(function (match) {
      var value = match[1];
      if (PLACEHOLDERS.indexOf(value.toLowerCase().replace(/^["']|["']$/g, "")) !== -1) { return; }
      var distinct = {};
      for (var k = 0; k < value.length; k += 1) { distinct[value.charAt(k)] = true; }
      if (Object.keys(distinct).length < 3) { return; }
      var offset = match[0].lastIndexOf(value);
      spans.push(span(match.index + offset, match.index + offset + value.length,
        "SECRET", value, "assignment", 0.9,
        "value assigned to a credential-shaped key"));
    });
    return spans;
  }

  function detectEmails(text) {
    return matchAll(text, EMAIL_RE).filter(function (m) {
      return emailValid(m[0]);
    }).map(function (m) {
      return span(m.index, m.index + m[0].length, "EMAIL", m[0], "email", 0.98,
        "structurally valid address");
    });
  }

  function detectCards(text) {
    var spans = [];
    matchAll(text, CARD_RE).forEach(function (m) {
      var digits = digitsOnly(m[0]);
      if (digits.length < 13 || digits.length > 19) { return; }
      if (!luhnValid(digits)) { return; }
      spans.push(span(m.index, m.index + m[0].length, "CREDIT_CARD", m[0],
        "luhn", 0.97, "passes the Luhn checksum"));
    });
    return spans;
  }

  function detectIbans(text) {
    var spans = [];
    matchAll(text, IBAN_RE).forEach(function (m) {
      if (!ibanValid(m[0])) { return; }
      spans.push(span(m.index, m.index + m[0].length, "IBAN", m[0], "mod97",
        0.97, "passes the ISO 7064 mod-97 check"));
    });
    return spans;
  }

  function detectSsns(text) {
    var spans = [];
    matchAll(text, SSN_RE).forEach(function (m) {
      if (!ssnValid(digitsOnly(m[0]))) { return; }
      spans.push(span(m.index, m.index + m[0].length, "SSN", m[0], "ssa_rules",
        0.95, "valid SSA area/group/serial"));
    });
    return spans;
  }

  function detectNationalIds(text) {
    return matchAll(text, NATIONAL_ID_RE).map(function (m) {
      return span(m.index, m.index + m[0].length, "NATIONAL_ID", m[0], "cnic",
        0.95, "matches the CNIC format #####-#######-#");
    });
  }

  function detectIpv4(text) {
    var spans = [];
    matchAll(text, IPV4_RE).forEach(function (m) {
      var before = text.charAt(m.index - 1), after = text.charAt(m.index + m[0].length);
      if (/[\d.]/.test(before) || /[\d.]/.test(after)) { return; }
      if (!ipv4Valid([m[1], m[2], m[3], m[4]])) { return; }
      spans.push(span(m.index, m.index + m[0].length, "IPV4", m[0], "octets",
        0.95, "all four octets in range 0-255"));
    });
    return spans;
  }

  function detectIpv6(text) {
    var spans = [];
    // Ported alternative-for-alternative from IPV6_RE in veil/detectors.py.
    // The boundaries are checked by hand rather than with a lookbehind: the
    // lookbehind is supported everywhere modern, but it is a *parse-time*
    // construct, so an older engine would reject the whole file rather than
    // just this detector. Checking two characters is the cheaper risk.
    var hextet = "[0-9A-Fa-f]{1,4}";
    var regex = new RegExp(
      "(?:" +
      "(?:" + hextet + ":){7}" + hextet + "|" +
      "(?:" + hextet + ":){1,7}:|" +
      "(?:" + hextet + ":){1,6}:" + hextet + "|" +
      "(?:" + hextet + ":){1,5}(?::" + hextet + "){1,2}|" +
      "(?:" + hextet + ":){1,4}(?::" + hextet + "){1,3}|" +
      "(?:" + hextet + ":){1,3}(?::" + hextet + "){1,4}|" +
      "(?:" + hextet + ":){1,2}(?::" + hextet + "){1,5}|" +
      hextet + ":(?::" + hextet + "){1,6}|" +
      ":(?:(?::" + hextet + "){1,7}|:)" +
      ")", "g");

    matchAll(text, regex).forEach(function (m) {
      var value = m[0];
      // A real IPv6 literal either compresses with "::" or writes all eight
      // hextets (seven colons). The alternation alone can also match a plain
      // six-group sequence, which is a MAC address and belongs to that
      // detector, so this guard is what keeps the two apart.
      var colons = (value.match(/:/g) || []).length;
      if (value.indexOf("::") === -1 && colons !== 7) { return; }
      var before = text.charAt(m.index - 1);
      var after = text.charAt(m.index + value.length);
      if (before && /[\w:.]/.test(before)) { return; }
      if (after && /[\w:.]/.test(after)) { return; }
      spans.push(span(m.index, m.index + value.length, "IPV6", value, "hextets",
        0.92, "two or more hextet groups"));
    });
    return spans;
  }

  function detectMacs(text) {
    var spans = [];
    matchAll(text, MAC_RE).forEach(function (m) {
      var before = text.charAt(m.index - 1), after = text.charAt(m.index + m[0].length);
      if (before && /[\w:]/.test(before)) { return; }
      if (after && /[\w:]/.test(after)) { return; }
      spans.push(span(m.index, m.index + m[0].length, "MAC", m[0], "six_octets",
        0.95, "six hex octets"));
    });
    return spans;
  }

  function detectUrls(text) {
    var spans = [];
    matchAll(text, URL_RE).forEach(function (m) {
      var value = m[0].replace(/[.,;:!?]+$/, "");
      if (value.split("(").length !== value.split(")").length) {
        value = value.replace(/\)+$/, "");
      }
      var host = value.split("//")[1].split("/")[0].split("?")[0];
      host = host.split("@").pop().split(":")[0];
      if (host.indexOf(".") === -1 && host !== "localhost") { return; }
      spans.push(span(m.index, m.index + value.length, "URL", value, "url", 0.9,
        "http(s) URL with a dotted host"));
    });
    return spans;
  }

  function hasContext(text, start, end, words, before, after) {
    var pre = text.slice(Math.max(0, start - before), start).toLowerCase();
    var post = text.slice(end, end + after).toLowerCase();
    for (var i = 0; i < words.length; i += 1) {
      var w = words[i];
      if (new RegExp("(^|[^a-z0-9])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") +
                     "($|[^a-z0-9])").test(pre)) { return true; }
      if (new RegExp("(^|[^a-z0-9])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") +
                     "($|[^a-z0-9])").test(post)) { return true; }
    }
    return false;
  }

  function detectPhones(text) {
    var spans = [];
    matchAll(text, PHONE_RE).forEach(function (m) {
      var raw = m[0];
      var d = digitsOnly(raw);
      if (d.length < 7 || d.length > 15) { return; }
      if (isIpv4Shape(raw) || isDateShape(raw)) { return; }
      var before = text.charAt(m.index - 1);
      if (before && /[\w.\-+]/.test(before)) { return; }
      var after = text.charAt(m.index + raw.length);
      if (after && /[\w]/.test(after)) { return; }
      var hasPlus = /^\s*\+/.test(raw);
      var hasParen = raw.indexOf("(") !== -1;
      var separators = 0;
      for (var i = 0; i < raw.length; i += 1) {
        if (" -().".indexOf(raw.charAt(i)) !== -1) { separators += 1; }
      }
      var ok = hasPlus || hasParen || separators >= 2;
      var note;
      if (ok) {
        note = hasPlus ? "leading + country code"
          : (hasParen ? "parenthesised area code" : separators + " separators");
      } else if (hasContext(text, m.index, m.index + raw.length, PHONE_CONTEXT, 32, 12)) {
        ok = true;
        note = "phone keyword nearby";
      } else {
        return;
      }
      spans.push(span(m.index, m.index + raw.length, "PHONE", raw, "e164_shape",
        0.85, note));
    });
    return spans;
  }

  function detectDob(text) {
    var candidates = [], m;
    matchAll(text, DOB_ISO_RE).forEach(function (x) {
      if (validDateParts(+x[1], +x[2], +x[3])) {
        candidates.push([x.index, x.index + x[0].length, x[0]]);
      }
    });
    matchAll(text, DOB_SLASH_RE).forEach(function (x) {
      if (validDateParts(+x[3], +x[1], +x[2])) {
        candidates.push([x.index, x.index + x[0].length, x[0]]);
      }
    });
    var monthPattern = new RegExp("\\b(\\d{1,2})[ \\t]+(" + MONTHS.join("|") + ")\\.?[ \\t]+(\\d{4})\\b", "gi");
    matchAll(text, monthPattern).forEach(function (x) {
      var day = +x[1], year = +x[3];
      if (validDateParts(year, 1, day)) {
        candidates.push([x.index, x.index + x[0].length, x[0]]);
      }
    });

    var spans = [];
    candidates.forEach(function (c) {
      if (!hasContext(text, c[0], c[1], BIRTH_CONTEXT, 24, 12)) { return; }
      spans.push(span(c[0], c[1], "DATE_OF_BIRTH", c[2], "birth_context", 0.88,
        "date next to a birth-context keyword"));
    });
    return spans;
  }

  function detectPersons(text, extraNames) {
    var spans = [];
    matchAll(text, HONORIFIC_RE).forEach(function (m) {
      spans.push(span(m.index, m.index + m[0].length, "PERSON", m[0], "honorific",
        0.9, "preceded by an honorific"));
    });
    var names = FIRST_NAMES.slice();
    (extraNames || []).forEach(function (n) {
      if (n) { uniquePush(names, n.toLowerCase()); }
    });
    if (names.length) {
      names.sort(function (a, b) { return b.length - a.length; });
      // Python scopes case-insensitivity to the given-name group with (?i:...)
      // so the surname stays case-SENSITIVE, which is what stops ordinary
      // lowercase prose from becoming a person. JavaScript has no inline (?i:)
      // group, so the whole pattern is case-insensitive and the surname is
      // re-checked case-sensitively below — the same net rule, checked in a
      // different place.
      var pattern = new RegExp("\\b(?:" + names.join("|") +
        ")[ \\t]+([A-Za-z][a-z]{1,20})\\b", "gi");
      matchAll(text, pattern).forEach(function (m) {
        if (!/^[A-Z][a-z]{1,20}$/.test(m[1])) { return; }
        spans.push(span(m.index, m.index + m[0].length, "PERSON", m[0],
          "name_dictionary", 0.7, "known given name followed by a surname"));
      });
    }
    return spans;
  }

  var DETECTOR_ORDER = ["SECRET", "CREDIT_CARD", "SSN", "IBAN", "NATIONAL_ID",
    "EMAIL", "PHONE", "DATE_OF_BIRTH", "IPV6", "IPV4", "MAC", "URL", "PERSON"];

  function detectAll(text, enabled, extraNames) {
    var active = {};
    if (enabled === undefined || enabled === null) {
      DETECTOR_ORDER.forEach(function (e) { if (e !== "PERSON") { active[e] = true; } });
    } else if (enabled.indexOf("ALL") !== -1 || enabled.indexOf("all") !== -1) {
      DETECTOR_ORDER.forEach(function (e) { active[e] = true; });
    } else {
      enabled.forEach(function (e) { active[String(e).toUpperCase()] = true; });
    }

    var found = [];
    if (active.SECRET) { found = found.concat(detectSecrets(text)); }
    if (active.CREDIT_CARD) { found = found.concat(detectCards(text)); }
    if (active.SSN) { found = found.concat(detectSsns(text)); }
    if (active.IBAN) { found = found.concat(detectIbans(text)); }
    if (active.NATIONAL_ID) { found = found.concat(detectNationalIds(text)); }
    if (active.EMAIL) { found = found.concat(detectEmails(text)); }
    if (active.PHONE) { found = found.concat(detectPhones(text)); }
    if (active.DATE_OF_BIRTH) { found = found.concat(detectDob(text)); }
    if (active.IPV6) { found = found.concat(detectIpv6(text)); }
    if (active.IPV4) { found = found.concat(detectIpv4(text)); }
    if (active.MAC) { found = found.concat(detectMacs(text)); }
    if (active.URL) { found = found.concat(detectUrls(text)); }
    if (active.PERSON) { found = found.concat(detectPersons(text, extraNames)); }

    found.sort(function (a, b) {
      if (a.start !== b.start) { return a.start - b.start; }
      if (a.end !== b.end) { return a.end - b.end; }
      if (a.entity !== b.entity) { return a.entity < b.entity ? -1 : 1; }
      return a.detector < b.detector ? -1 : 1;
    });
    return found;
  }

  /* -------------------------------------------------------- overlap rules */

  function resolveSpans(spans) {
    var ordered = spans.slice().sort(function (a, b) {
      var lenA = a.end - a.start, lenB = b.end - b.start;
      if (lenA !== lenB) { return lenB - lenA; }
      if (a.confidence !== b.confidence) { return b.confidence - a.confidence; }
      var wA = RISK_WEIGHTS[a.entity] || 1, wB = RISK_WEIGHTS[b.entity] || 1;
      if (wA !== wB) { return wB - wA; }
      if (a.entity !== b.entity) { return a.entity < b.entity ? -1 : 1; }
      return a.start - b.start;
    });
    var chosen = [];
    ordered.forEach(function (candidate) {
      var overlaps = chosen.some(function (existing) {
        return candidate.start < existing.end && existing.start < candidate.end;
      });
      if (!overlaps) { chosen.push(candidate); }
    });
    chosen.sort(function (a, b) {
      if (a.start !== b.start) { return a.start - b.start; }
      return a.end - b.end;
    });
    return chosen;
  }

  /* ------------------------------------------------------------- actions */

  function maskValue(value, keepFirst, keepLast, maskChar) {
    keepFirst = keepFirst || 0;
    keepLast = keepLast === undefined ? 4 : keepLast;
    maskChar = maskChar || MASK_CHAR;
    if (value.length <= keepFirst + keepLast || value.length < MIN_PARTIAL_LENGTH) {
      return new Array(Math.max(value.length, 1) + 1).join(maskChar);
    }
    var head = keepFirst ? value.slice(0, keepFirst) : "";
    var tail = keepLast ? value.slice(value.length - keepLast) : "";
    var hidden = value.length - head.length - tail.length;
    if (hidden < 3) { return new Array(value.length + 1).join(maskChar); }
    return head + new Array(hidden + 1).join(maskChar) + tail;
  }

  /* ------------------------------------------------------------- SHA-256
   * A hand-rolled SHA-256 so the browser can compute exactly the same HMAC as
   * Python's hmac.new(key, msg, hashlib.sha256). This is not gold-plating: the
   * `hash` action exists so the SAME value produces the SAME digest wherever it
   * is processed. A different digest algorithm in the browser would silently
   * break that, and a token produced in the page could never be correlated with
   * one produced by the CLI. tools/parity_check.py asserts the digests match.
   */

  var SHA256_K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function utf8Bytes(str) {
    if (typeof TextEncoder !== "undefined") { return new TextEncoder().encode(str); }
    var out = [];
    for (var i = 0; i < str.length; i += 1) {
      var c = str.charCodeAt(i);
      if (c < 0x80) { out.push(c); }
      else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 63)); }
      else { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)); }
    }
    return new Uint8Array(out);
  }

  function concatBytes(a, b) {
    var out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
  }

  function sha256(bytes) {
    var H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var len = bytes.length;
    var bitLen = len * 8;
    var withOne = len + 1;
    var padded = withOne + (((56 - (withOne % 64)) + 64) % 64) + 8;

    var buf = new Uint8Array(padded);
    buf.set(bytes, 0);
    buf[len] = 0x80;
    var hi = Math.floor(bitLen / 0x100000000);
    var lo = bitLen >>> 0;
    buf[padded - 8] = (hi >>> 24) & 0xff;
    buf[padded - 7] = (hi >>> 16) & 0xff;
    buf[padded - 6] = (hi >>> 8) & 0xff;
    buf[padded - 5] = hi & 0xff;
    buf[padded - 4] = (lo >>> 24) & 0xff;
    buf[padded - 3] = (lo >>> 16) & 0xff;
    buf[padded - 2] = (lo >>> 8) & 0xff;
    buf[padded - 1] = lo & 0xff;

    var w = new Array(64);
    for (var offset = 0; offset < padded; offset += 64) {
      var i, s0, s1;
      for (i = 0; i < 16; i += 1) {
        w[i] = (buf[offset + i * 4] << 24) | (buf[offset + i * 4 + 1] << 16) |
               (buf[offset + i * 4 + 2] << 8) | (buf[offset + i * 4 + 3]);
      }
      for (i = 16; i < 64; i += 1) {
        s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3];
      var e = H[4], f = H[5], g = H[6], h = H[7];
      for (i = 0; i < 64; i += 1) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (h + S1 + ch + SHA256_K[i] + w[i]) | 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0;
      H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0;
      H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }

    var out = new Uint8Array(32);
    for (var j = 0; j < 8; j += 1) {
      out[j * 4] = (H[j] >>> 24) & 0xff;
      out[j * 4 + 1] = (H[j] >>> 16) & 0xff;
      out[j * 4 + 2] = (H[j] >>> 8) & 0xff;
      out[j * 4 + 3] = H[j] & 0xff;
    }
    return out;
  }

  function toHex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i += 1) {
      out += ("0" + bytes[i].toString(16)).slice(-2);
    }
    return out;
  }

  function hmacSha256Hex(key, message) {
    var blockSize = 64;
    var keyBytes = utf8Bytes(key || "");
    if (keyBytes.length > blockSize) { keyBytes = sha256(keyBytes); }
    var paddedKey = new Uint8Array(blockSize);
    paddedKey.set(keyBytes, 0);

    var oKeyPad = new Uint8Array(blockSize);
    var iKeyPad = new Uint8Array(blockSize);
    for (var i = 0; i < blockSize; i += 1) {
      oKeyPad[i] = paddedKey[i] ^ 0x5c;
      iKeyPad[i] = paddedKey[i] ^ 0x36;
    }
    var inner = sha256(concatBytes(iKeyPad, utf8Bytes(message)));
    return toHex(sha256(concatBytes(oKeyPad, inner)));
  }

  function hashValue(value, salt) {
    return hmacSha256Hex(salt || "", value).slice(0, HASH_LENGTH);
  }

  function Tokenizer(prefix) {
    this.prefix = prefix || "VEIL";
    this.counters = {};
    this.tokens = {};
    this.values = {};
  }
  Tokenizer.prototype.tokenFor = function (entity, value) {
    if (this.tokens[value]) { return this.tokens[value]; }
    var index = (this.counters[entity] || 0) + 1;
    this.counters[entity] = index;
    var token = this.prefix + "_" + entity + "_" + ("00" + index).slice(-3);
    this.tokens[value] = token;
    this.values[token] = value;
    return token;
  };
  Tokenizer.prototype.mapping = function () { return this.values; };

  function applyAction(action, value, entity, options) {
    options = options || {};
    action = String(action || "").toLowerCase();
    if (ACTIONS.indexOf(action) === -1) {
      throw new Error("unknown action " + JSON.stringify(action));
    }
    if (action === "keep") { return value; }
    if (action === "mask") {
      return maskValue(value, options.keepFirst, options.keepLast, options.maskChar);
    }
    if (action === "redact") { return "[" + entity + "]"; }
    if (action === "remove") { return ""; }
    if (action === "hash") {
      return "[" + entity + ":" + hashValue(value, options.salt) + "]";
    }
    if (action === "tokenize") {
      if (!options.tokenizer) { throw new Error("tokenize needs a Tokenizer"); }
      return options.tokenizer.tokenFor(entity, value);
    }
    throw new Error("action " + JSON.stringify(action) + " is not implemented");
  }

  /* ------------------------------------------------------------- scanning */

  function defaultPolicy() {
    return {
      name: "default",
      entities: DEFAULT_ENTITIES.slice(),
      actions: Object.assign({}, DEFAULT_ACTIONS),
      keepFirst: 0,
      keepLast: 4,
      maskChar: MASK_CHAR,
      hashSalt: "",
      tokenPrefix: "VEIL",
      allowlist: [],
      denylist: [],
      names: [],
      caseSensitiveDenylist: false
    };
  }

  function denylistSpans(text, policy) {
    var spans = [];
    policy.denylist.forEach(function (literal) {
      if (!literal) { return; }
      var haystack = policy.caseSensitiveDenylist ? text : text.toLowerCase();
      var needle = policy.caseSensitiveDenylist ? literal : literal.toLowerCase();
      var from = haystack.indexOf(needle);
      while (from !== -1) {
        spans.push(span(from, from + needle.length, "EXACT_MATCH",
          text.slice(from, from + needle.length), "denylist", 1.0,
          "listed verbatim in the policy denylist"));
        from = haystack.indexOf(needle, from + needle.length);
      }
    });
    return spans;
  }

  function detect(text, policy) {
    var spans = detectAll(text, policy.entities, policy.names);
    spans = spans.concat(denylistSpans(text, policy));
    return resolveSpans(spans);
  }

  function scoreRisk(findings) {
    var total = 0, byEntity = {}, occurrences = 0, distinct = 0;
    findings.forEach(function (f) {
      if (f.preserved) { return; }
      var weight = RISK_WEIGHTS[f.entity] || 1;
      total += weight * (1 + Math.log(Math.max(f.count, 1)) / Math.LN2);
      byEntity[f.entity] = (byEntity[f.entity] || 0) + f.count;
      occurrences += f.count;
      distinct += 1;
    });
    var score = Math.round(total), level = "NONE";
    for (var i = 0; i < RISK_BANDS.length; i += 1) {
      if (score >= RISK_BANDS[i][0]) { level = RISK_BANDS[i][1]; break; }
    }
    return {
      score: score, level: level, distinct_findings: distinct,
      total_occurrences: occurrences, by_entity: byEntity
    };
  }

  function group(text, spans, policy, tokenizer) {
    var replacements = {}, actions = {}, findings = {}, order = [];
    var allow = {};
    policy.allowlist.forEach(function (v) { if (v) { allow[v] = true; } });

    spans.forEach(function (s) {
      var key = s.entity + "\u0000" + s.value;
      if (replacements[key] === undefined) {
        var action = allow[s.value] ? "keep" : (policy.actions[s.entity] || "redact");
        actions[key] = action;
        replacements[key] = applyAction(action, s.value, s.entity, {
          tokenizer: tokenizer, keepFirst: policy.keepFirst,
          keepLast: policy.keepLast, maskChar: policy.maskChar,
          salt: policy.hashSalt
        });
        findings[key] = {
          entity: s.entity, value: s.value, action: action,
          replacement: replacements[key], count: 0,
          detector: s.detector, confidence: s.confidence, note: s.note,
          preserved: action === "keep", offsets: []
        };
        order.push(key);
      }
      findings[key].count += 1;
      findings[key].offsets.push([s.start, s.end]);
    });

    var list = order.map(function (key) { return findings[key]; });
    list.sort(function (a, b) {
      var wA = RISK_WEIGHTS[a.entity] || 1, wB = RISK_WEIGHTS[b.entity] || 1;
      if (wA !== wB) { return wB - wA; }
      if (a.entity !== b.entity) { return a.entity < b.entity ? -1 : 1; }
      return a.offsets[0][0] - b.offsets[0][0];
    });
    return { replacements: replacements, findings: list };
  }

  function scan(text, policy) {
    policy = policy || defaultPolicy();
    var spans = detect(text, policy);
    var grouped = group(text, spans, policy, null);
    return {
      policy: policy.name,
      text: text,
      findings: grouped.findings,
      risk: scoreRisk(grouped.findings),
      isRedacted: false,
      tokenMap: {}
    };
  }

  function verifyOutput(redacted, policy) {
    // Recognise the tool's own tokens so a token is never mistaken for a
    // residual. Built from the entity catalogue rather than a loose [A-Z_]+,
    // because entity names legitimately contain digits (IPV4, IPV6) and a
    // digits-free pattern would miss those tokens and report a clean
    // redaction as dirty.
    var tokenEntities = ALL_ENTITIES.concat(["EXACT_MATCH"]).join("|");
    var tokenPattern = new RegExp("^" + policy.tokenPrefix +
      "_(" + tokenEntities + ")_\\d+$");
    var allow = {};
    policy.allowlist.forEach(function (v) { if (v) { allow[v] = true; } });
    var rescan = detectAll(redacted, policy.entities, policy.names);
    var residuals = [], preserved = [];
    rescan.forEach(function (s) {
      if (tokenPattern.test(s.value)) { return; }
      var record = {
        entity: s.entity, value: s.value, start: s.start, detector: s.detector
      };
      if (allow[s.value]) { preserved.push(record); } else { residuals.push(record); }
    });
    return {
      status: residuals.length ? "RESIDUAL_FOUND" : "CLEAN",
      clean: residuals.length === 0,
      checked_entities: policy.entities.slice(),
      residuals: residuals,
      preserved: preserved
    };
  }

  function redact(text, policy, options) {
    policy = policy || defaultPolicy();
    options = options || {};
    var tokenizer = new Tokenizer(policy.tokenPrefix);
    var spans = detect(text, policy);
    var grouped = group(text, spans, policy, tokenizer);

    var pieces = [], cursor = 0;
    spans.forEach(function (s) {
      pieces.push(text.slice(cursor, s.start));
      pieces.push(grouped.replacements[s.entity + "\u0000" + s.value]);
      cursor = s.end;
    });
    pieces.push(text.slice(cursor));
    var out = pieces.join("");

    var result = {
      policy: policy.name,
      text: out,
      findings: grouped.findings,
      risk: scoreRisk(grouped.findings),
      isRedacted: true,
      tokenMap: tokenizer.mapping()
    };
    if (options.verify !== false) {
      result.verification = verifyOutput(out, policy);
    }
    return result;
  }

  function detokenize(text, mapping) {
    var tokens = Object.keys(mapping || {}).sort(function (a, b) {
      return b.length - a.length;
    });
    var out = text;
    tokens.forEach(function (token) {
      out = out.split(token).join(mapping[token]);
    });
    return out;
  }

  function policyFromSpec(spec) {
    var policy = defaultPolicy();
    if (!spec) { return policy; }
    Object.keys(spec).forEach(function (key) {
      if (spec[key] === null || spec[key] === undefined) { return; }
      if (key === "name") { policy.name = String(spec[key]); }
      else if (key === "entities") {
        var resolved = [];
        spec[key].forEach(function (raw) {
          var token = String(raw).trim().toUpperCase();
          if (token === "ALL") { resolved = ALL_ENTITIES.slice(); return; }
          if (token.charAt(0) === "!") {
            resolved = resolved.filter(function (e) { return e !== token.slice(1); });
            return;
          }
          uniquePush(resolved, token);
        });
        policy.entities = resolved;
      } else if (key === "actions") {
        Object.keys(spec[key]).forEach(function (entity) {
          policy.actions[String(entity).toUpperCase()] = String(spec[key][entity]).toLowerCase();
        });
      } else if (key === "keep_first") { policy.keepFirst = spec[key]; }
      else if (key === "keep_last") { policy.keepLast = spec[key]; }
      else if (key === "mask_char") { policy.maskChar = spec[key]; }
      else if (key === "hash_salt") { policy.hashSalt = spec[key]; }
      else if (key === "token_prefix") { policy.tokenPrefix = spec[key]; }
      else if (key === "allowlist") { policy.allowlist = spec[key].slice(); }
      else if (key === "denylist") { policy.denylist = spec[key].slice(); }
      else if (key === "names") { policy.names = spec[key].slice(); }
      else if (key === "case_sensitive_denylist") {
        policy.caseSensitiveDenylist = !!spec[key];
      }
    });
    return policy;
  }

  var VEIL = {
    version: "1.0.0",
    ALL_ENTITIES: ALL_ENTITIES,
    DEFAULT_ENTITIES: DEFAULT_ENTITIES,
    DEFAULT_ACTIONS: DEFAULT_ACTIONS,
    ACTIONS: ACTIONS,
    RISK_WEIGHTS: RISK_WEIGHTS,
    RISK_BANDS: RISK_BANDS,
    OPT_IN: OPT_IN,
    DETECTOR_ORDER: DETECTOR_ORDER,
    MASK_CHAR: MASK_CHAR,
    defaultPolicy: defaultPolicy,
    policyFromSpec: policyFromSpec,
    detectAll: detectAll,
    detect: detect,
    resolveSpans: resolveSpans,
    scan: scan,
    redact: redact,
    verifyOutput: verifyOutput,
    detokenize: detokenize,
    scoreRisk: scoreRisk,
    maskValue: maskValue,
    hashValue: hashValue,
    Tokenizer: Tokenizer,
    luhnValid: luhnValid,
    ibanValid: ibanValid,
    ssnValid: ssnValid,
    ipv4Valid: ipv4Valid,
    emailValid: emailValid
  };

  root.VEIL = VEIL;
  if (typeof module !== "undefined" && module.exports) { module.exports = VEIL; }
}(typeof window !== "undefined" ? window : globalThis));
