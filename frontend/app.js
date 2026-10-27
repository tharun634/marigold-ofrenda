const peopleEl = document.querySelector("#people");
const statusEl = document.querySelector("#status");
const answerEl = document.querySelector("#answer");
const answerText = document.querySelector("#answer-text");
const answerModels = document.querySelector("#answer-models");
const sourcesEl = document.querySelector("#sources");
const questionEl = document.querySelector("#question");
const askButton = document.querySelector("#ask-button");

let selectedId = null;
let health = null;

function setStatus(text) {
  statusEl.textContent = text;
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : "The altar could not do that.";
    throw new Error(message);
  }
  return payload;
}

function card(person) {
  const article = document.createElement("article");
  article.className = "card" + (person.id === selectedId ? " selected" : "");
  article.dataset.id = String(person.id);

  const header = document.createElement("header");
  const titleWrap = document.createElement("div");
  const name = document.createElement("h3");
  name.textContent = person.display_name;
  const relation = document.createElement("p");
  relation.className = "relation";
  relation.textContent = person.relationship || "Remembered here";
  titleWrap.append(name, relation);

  const side = document.createElement("div");
  const years = document.createElement("div");
  years.className = "years";
  years.textContent = person.years_lived || "";
  const candle = document.createElement("div");
  candle.className = "candle";
  candle.title = `${person.candle_count} candles`;
  side.append(years, candle);
  header.append(titleWrap, side);

  const offerings = document.createElement("ul");
  offerings.className = "offerings";
  for (const item of [person.favorite_food, person.favorite_song, person.offering]) {
    if (!item) continue;
    const li = document.createElement("li");
    li.textContent = item;
    offerings.append(li);
  }

  const actions = document.createElement("div");
  actions.className = "card-actions";
  const choose = document.createElement("button");
  choose.type = "button";
  choose.className = "secondary";
  choose.textContent = person.id === selectedId ? "Asking about them" : "Ask about them";
  choose.addEventListener("click", () => {
    selectedId = selectedId === person.id ? null : person.id;
    renderPeople(lastPeople);
  });
  const light = document.createElement("button");
  light.type = "button";
  light.textContent = `Light a candle (${person.candle_count})`;
  light.addEventListener("click", () => lightCandle(person, light));
  actions.append(choose, light);

  const counts = document.createElement("p");
  counts.className = "relation";
  counts.textContent = `${person.memory_count} memories on the altar`;

  article.append(header, offerings, counts, actions);
  return article;
}

let lastPeople = [];

function renderPeople(people) {
  lastPeople = people;
  peopleEl.replaceChildren(...people.map(card));
}

async function refresh() {
  const altar = await readJson(await fetch("/api/altar"));
  renderPeople(altar.people);
}

async function lightCandle(person, button) {
  button.disabled = true;
  try {
    await readJson(await fetch("/api/candles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beloved_id: person.id, whisper: "A candle for " + person.display_name }),
    }));
    await refresh();
  } catch (error) {
    setStatus(error.message);
  }
}

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    questionEl.value = button.dataset.question;
    document.querySelector("#ask-form").requestSubmit();
  });
});

document.querySelector("#ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  askButton.disabled = true;
  askButton.textContent = "Listening…";
  answerEl.hidden = false;
  answerText.textContent = "The candle leans toward the page.";
  sourcesEl.replaceChildren();
  try {
    const payload = await readJson(await fetch("/api/seance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: questionEl.value,
        beloved_id: selectedId,
      }),
    }));
    answerText.textContent = payload.answer;
    answerModels.textContent = `${payload.chat_model} · ${payload.embed_model}`;
    sourcesEl.replaceChildren(...payload.sources.map((source) => {
      const li = document.createElement("li");
      const strong = document.createElement("strong");
      strong.textContent = source.display_name;
      li.append(strong, document.createTextNode(" — " + source.body));
      const rank = document.createElement("div");
      rank.className = "rank";
      const keyword = source.keyword_rank ? `keyword #${source.keyword_rank}` : "keyword miss";
      const semantic = source.semantic_rank ? `meaning #${source.semantic_rank}` : "meaning miss";
      rank.textContent = `${keyword} · ${semantic}`;
      li.append(rank);
      return li;
    }));
  } catch (error) {
    answerText.textContent = error.message;
  } finally {
    askButton.disabled = false;
    askButton.textContent = "Listen";
  }
});

document.querySelector("#memory-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedId) {
    setStatus("Choose someone on the altar before adding a memory.");
    return;
  }
  const body = document.querySelector("#memory-body");
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await readJson(await fetch("/api/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beloved_id: selectedId, body: body.value }),
    }));
    body.value = "";
    await refresh();
    setStatus("The memory is on the altar.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#person-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form).entries());
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const created = await readJson(await fetch("/api/beloved", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }));
    form.reset();
    selectedId = created.id;
    await refresh();
    setStatus(created.display_name + " is on the altar.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
  }
});

async function boot() {
  try {
    health = await readJson(await fetch("/api/health"));
    const bits = [
      health.database ? "Tiger Cloud" : "database offline",
      health.ollama ? health.chat_model : "Ollama offline",
    ];
    setStatus(bits.join(" · "));
    await refresh();
  } catch (error) {
    setStatus(error.message);
  }
}

boot();
