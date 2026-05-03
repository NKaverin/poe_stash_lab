#include <iostream>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>
#include <stdexcept>
#include <chrono>
#include <cstdlib>
#include <functional>
#include <ctime>

#include <simdjson.h>
#include <httplib.h>

namespace fs = std::filesystem;
using namespace simdjson;

// --- types -------------------------------------------------------------------

struct Dataset {
    std::string league_id;
    std::string league;
    bool        hardcore;
    std::string file;

    std::string index_name() const { return "poe-data-" + league_id; }
};

struct Manifest {
    std::string snapshot_at;
    std::string source;
    std::vector<Dataset> datasets;
};

// --- manifest reading -------------------------------------------

fs::path find_project_root() {
    for (fs::path p = fs::current_path(); p != p.parent_path(); p = p.parent_path()) {
        if (fs::exists(p / "pyproject.toml")) return p;
    }
    throw std::runtime_error("project root not found (no pyproject.toml above CWD)");
}

Manifest load_manifest(const fs::path& path) {
    ondemand::parser parser;
    auto json = padded_string::load(path.string());
    ondemand::document doc = parser.iterate(json);

    Manifest m;
    m.snapshot_at = std::string(std::string_view(doc["snapshot_at"]));
    m.source      = std::string(std::string_view(doc["source"]));

    for (auto ds : doc["datasets"]) {
        m.datasets.push_back({
            std::string(std::string_view(ds["league_id"])),
            std::string(std::string_view(ds["league"])),
            bool(ds["hardcore"]),
            std::string(std::string_view(ds["file"])),
        });
    }
    return m;
}

// --- per-item id extraction -------------------------------------

int64_t extract_id(std::string_view raw_item) {
    auto pos = raw_item.find("\"id\":");
    if (pos == std::string_view::npos) {
        throw std::runtime_error("item missing \"id\" field");
    }
    pos += 5;
    char* end = nullptr;
    long long val = std::strtoll(raw_item.data() + pos, &end, 10);
    if (end == raw_item.data() + pos) {
        throw std::runtime_error("item id is not a number");
    }
    return val;
}

// --- bulk body building -----------------------------------------

constexpr size_t CHUNK_MAX_BYTES = 10 * 1024 * 1024;
constexpr size_t CHUNK_MAX_DOCS  = 1000;

using FlushFn = std::function<void(const std::string&, size_t)>;

void process_dataset(const fs::path& root, const Dataset& d,
                     std::string_view snapshot_at, FlushFn flush) {
    ondemand::parser parser;
    auto json = padded_string::load((root / d.file).string());
    ondemand::document doc = parser.iterate(json);

    std::string body;
    body.reserve(CHUNK_MAX_BYTES + 1024 * 1024);
    size_t docs_in_chunk = 0;

    const std::string index = d.index_name();
    const char* hc_str = d.hardcore ? "true" : "false";

    for (auto item_result : doc["items"]) {
        std::string_view raw = item_result.raw_json();
        int64_t id = extract_id(raw);

        body += R"({"index":{"_index":")";
        body += index;
        body += R"(","_id":")";
        body += d.league_id;
        body += '_';
        body += std::to_string(id);
        body += R"("}})";
        body += '\n';

        if (raw.empty() || raw.back() != '}') {
            throw std::runtime_error("item raw JSON not an object");
        }
        body.append(raw.data(), raw.size() - 1);
        body += R"(,"@timestamp":")";
        body += snapshot_at;
        body += R"(","league":")";
        body += d.league_id;
        body += R"(","hardcore":)";
        body += hc_str;
        body += R"(,"ingest_metadata":{"language":"cpp"}})";
        body += '\n';

        docs_in_chunk++;
        if (docs_in_chunk >= CHUNK_MAX_DOCS || body.size() >= CHUNK_MAX_BYTES) {
            flush(body, docs_in_chunk);
            body.clear();
            docs_in_chunk = 0;
        }
    }
    if (!body.empty()) flush(body, docs_in_chunk);
}

// --- HTTP layer --------------------------------------------------------------

// Counts (ok, failed) for a single _bulk response. ES returns "errors": false
// when every action succeeded; in that case we don't need to walk per-item.
struct ChunkOutcome { size_t ok; size_t failed; };

ChunkOutcome parse_bulk_response(ondemand::parser& p, const std::string& resp_body,
                                 size_t docs_sent) {
    auto padded = padded_string(resp_body);
    ondemand::document resp = p.iterate(padded);
    if (!bool(resp["errors"])) {
        return {docs_sent, 0};
    }
    size_t ok = 0, failed = 0;
    for (auto item : resp["items"]) {
        int64_t status = int64_t(item["index"]["status"]);
        if (status >= 200 && status < 300) ok++;
        else failed++;
    }
    return {ok, failed};
}

// --- bench record writing ---------------------------------------------------

// "2026-05-03T16:39:00Z" in UTC. Same shape as Python's iso8601 string.
// gmtime is fine here because we're single-threaded.
std::string iso_now_utc() {
    auto t = std::chrono::system_clock::to_time_t(
        std::chrono::system_clock::now());
    std::tm tm = *std::gmtime(&t);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return std::string(buf);
}

// One row per (tool, league_id, run). Same shape Python writes, so both
// languages' rows live in poe-bench together and Kibana can group on `tool`.
void write_bench_record(httplib::Client& cli, const std::string& league_id,
                        size_t ok, size_t failed, double duration_ms) {
    std::string body;
    body += R"({"@timestamp":")";
    body += iso_now_utc();
    body += R"(","tool":"cpp","league_id":")";
    body += league_id;
    body += R"(","doc_count":)";
    body += std::to_string(ok);
    body += R"(,"duration_ms":)";
    body += std::to_string(duration_ms);
    body += R"(,"failures":)";
    body += std::to_string(failed);
    body += '}';

    auto res = cli.Post("/poe-bench/_doc", body, "application/json");
    if (!res) {
        throw std::runtime_error(
            "bench write failed: " + httplib::to_string(res.error()));
    }
    if (res->status >= 300) {
        throw std::runtime_error(
            "bench write HTTP " + std::to_string(res->status) + ": " + res->body);
    }
}

// --- main --------------------------------------------------------------------

int main() {
    try {
        const char* env_url = std::getenv("ELASTICSEARCH_URL");
        std::string es_url = env_url ? env_url : "http://localhost:9200";

        httplib::Client cli(es_url);
        cli.set_connection_timeout(5);
        cli.set_read_timeout(60);
        cli.set_write_timeout(60);
        cli.set_keep_alive(true);  // re-use the TCP socket across chunks

        fs::path root = find_project_root();
        Manifest m = load_manifest(root / "data" / "raw" / "manifest.json");

        std::cout << "--- C++ bulk uploader (cpp) ---\n";
        std::cout << "es_url      = " << es_url << "\n";
        std::cout << "snapshot_at = " << m.snapshot_at << "\n";
        std::cout << "datasets    = " << m.datasets.size() << "\n";

        ondemand::parser resp_parser;  // reused for every chunk's response
        size_t total_failed = 0;

        for (const auto& d : m.datasets) {
            std::cout << "[UPLOAD] " << d.league_id << " -> " << d.index_name() << "\n";
            size_t ok = 0, failed = 0;

            auto t0 = std::chrono::steady_clock::now();
            process_dataset(root, d, m.snapshot_at,
                [&](const std::string& body, size_t docs) {
                    auto res = cli.Post("/_bulk", body, "application/x-ndjson");
                    if (!res) {
                        throw std::runtime_error(
                            "HTTP request failed: " + httplib::to_string(res.error()));
                    }
                    if (res->status != 200) {
                        throw std::runtime_error(
                            "HTTP " + std::to_string(res->status) + ": " + res->body);
                    }
                    auto outcome = parse_bulk_response(resp_parser, res->body, docs);
                    ok += outcome.ok;
                    failed += outcome.failed;
                    if (outcome.failed > 0) {
                        std::cerr << "  [WARN] " << outcome.failed
                                  << " doc(s) failed in this chunk\n";
                    }
                });
            auto t1 = std::chrono::steady_clock::now();
            double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
            double rate = ok / (ms / 1000.0);

            std::cout << "  ok=" << ok
                      << " failed=" << failed
                      << " duration_ms=" << ms
                      << " (" << static_cast<int>(rate) << " docs/s)\n";
            
            write_bench_record(cli, d.league_id, ok, failed, ms);

            total_failed += failed;
        }

        std::cout << "--- Done (total failures: " << total_failed << ") ---\n";
        return total_failed > 0 ? 1 : 0;
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] " << e.what() << "\n";
        return 1;
    }
}