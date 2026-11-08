// 3D-ICE-Batch: steady-state 3D-ICE solves for many floorplans of one stack, factorising the system matrix once.
//
// The thermal conductance matrix of a stack with a uniform grid does not depend on its floorplans: floorplans only set
// where power enters (the source vector). This program parses the stack once, builds and factorises the system
// (thermal_data_build), then for each case replaces die floorplans in place and re-runs emulate_steady, which rebuilds
// the source vector and solves with the existing factorisation. 3D-ICE-Emulator does all of that once per case.
//
// usage: 3D-ICE-Batch <file.stk>       (run in the directory the stack's relative paths refer to)
// Protocol, one command per line on stdin; one reply line per command on stdout (3D-ICE's own messages go to stderr):
//   startup                          -> "READY <seconds> <rows> <cols>" | "ERR <message>" (then exit 1)
//   FLP <die instance> <file.flp>    -> "OK" | "ERR <message>"   replace the floorplan of a die instance
//   SOLVE <prefix> <element>...      -> "OK <seconds>" | "ERR <message>"
//                                       writes <prefix><element>.f64: rows x cols float64, little-endian, row-major,
//                                       rows along the width (y), row 0 at y = 0 (the layout of a 3D-ICE Tmap)
//   QUIT                             -> exits 0
// Paths may not contain whitespace. Only steady-state analysis on a uniform grid is accepted.
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// The 3D-ICE headers declare C linkage themselves, and inside it SuperLU_MT pulls in omp.h, whose C++ part may not
// have C linkage: include omp.h first so its include guard keeps it out of those extern "C" blocks.
#include <omp.h>

#include "analysis.h"
#include "floorplan.h"
#include "floorplan_element.h"
#include "floorplan_element_list.h"
#include "powers_queue.h"
#include "output.h"
#include "stack_description.h"
#include "stack_element.h"
#include "stack_element_list.h"
#include "stack_file_parser.h"
#include "thermal_data.h"

namespace {

using Clock = std::chrono::steady_clock;

double since(Clock::time_point t) { return std::chrono::duration<double>(Clock::now() - t).count(); }

FILE* g_reply = nullptr;  // the protocol channel: the original stdout

void reply(const std::string& line) {
    std::fputs(line.c_str(), g_reply);
    std::fputc('\n', g_reply);
    std::fflush(g_reply);
}

std::string one_line(std::string s) {
    for (char& c : s)
        if (c == '\n' || c == '\r') c = ' ';
    return s;
}

StackElement_t* find_element(StackDescription_t* stkd, const std::string& id) {
    for (StackElementListNode_t* n = stack_element_list_begin(&stkd->StackElements); n != nullptr;
         n = stack_element_list_next(n)) {
        StackElement_t* e = stack_element_list_data(n);
        if (id == e->Id) return e;
    }
    return nullptr;
}

struct Session {
    StackDescription_t stkd;
    Analysis_t analysis;
    Output_t output;
    ThermalData_t tdata;
    bool have_tdata = false;

    Session() {
        stack_description_init(&stkd);
        analysis_init(&analysis);
        output_init(&output);
        thermal_data_init(&tdata);
    }
    ~Session() {
        if (have_tdata) thermal_data_destroy(&tdata);
        stack_description_destroy(&stkd);
        output_destroy(&output);
    }

    std::string open(const std::string& stk) {
        if (parse_stack_description_file(const_cast<char*>(stk.c_str()), &stkd, &analysis, &output) != TDICE_SUCCESS)
            return "cannot parse " + stk;
        if (analysis.AnalysisType != TDICE_ANALYSIS_TYPE_STEADY) return "only steady-state analysis is supported";
        if (stkd.Dimensions->NonUniform == 1) return "non-uniform grids are not supported";
        if (thermal_data_build(&tdata, &stkd.StackElements, stkd.Dimensions, &analysis, &stkd.Materials) !=
            TDICE_SUCCESS)
            return "thermal_data_build failed";
        have_tdata = true;
        // remember every die's floorplan file: a steady solve consumes each element's power value, so all floorplans
        // are reloaded before every solve (see solve)
        for (StackElementListNode_t* n = stack_element_list_begin(&stkd.StackElements); n != nullptr;
             n = stack_element_list_next(n)) {
            StackElement_t* e = stack_element_list_data(n);
            if (e->SEType == TDICE_STACK_ELEMENT_DIE) dies.push_back({e, e->Pointer.Die->Floorplan.FileName});
        }
        return "";
    }

    struct DieFloorplan {
        StackElement_t* element;
        std::string path;
    };
    std::vector<DieFloorplan> dies;

    std::string load(DieFloorplan& d, const std::string& path) {
        Floorplan_t* fp = &d.element->Pointer.Die->Floorplan;  // the power grid holds this address: refill in place
        floorplan_destroy(fp);
        if (fill_floorplan(fp, stkd.Dimensions, const_cast<char*>(path.c_str())) != TDICE_SUCCESS)
            return "cannot load floorplan " + path + " for " + d.element->Id;
        d.path = path;
        return "";
    }

    std::string set_floorplan(const std::string& instance, const std::string& path) {
        StackElement_t* e = find_element(&stkd, instance);
        if (e == nullptr) return "no stack element " + instance;
        for (auto& d : dies)
            if (d.element == e) return load(d, path);
        return instance + " is not a die";
    }

    std::string solve(const std::string& prefix, const std::vector<std::string>& elements, double& seconds) {
        std::vector<StackElement_t*> els;
        for (const auto& id : elements) {
            StackElement_t* e = find_element(&stkd, id);
            if (e == nullptr) return "no stack element " + id;
            els.push_back(e);
        }
        // Each element's power value is a queue that emulate_steady consumes; with an empty queue it prints a warning
        // and returns END_OF_SIMULATION without solving, leaving the previous temperatures in place. Reload every
        // floorplan so each queue holds exactly its file's value, and refuse to solve if one is still empty.
        for (auto& d : dies) {
            const std::string path = d.path;
            const std::string err = load(d, path);
            if (!err.empty()) return err;
            for (FloorplanElementListNode_t* n = floorplan_element_list_begin(&d.element->Pointer.Die->Floorplan.ElementsList);
                 n != nullptr; n = floorplan_element_list_next(n))
                if (is_empty_powers_queue(floorplan_element_list_data(n)->PowerValues))
                    return "floorplan " + path + " has an element without power values";
        }
        const auto t = Clock::now();
        const SimResult_t r = emulate_steady(&tdata, stkd.Dimensions, &analysis);
        seconds = since(t);
        if (r != TDICE_END_OF_SIMULATION) return "emulate_steady failed (code " + std::to_string(int(r)) + ")";
        Dimensions_t* d = stkd.Dimensions;
        const CellIndex_t rows = get_number_of_rows(d), cols = get_number_of_columns(d);
        std::vector<double> buf(static_cast<size_t>(rows) * cols);
        for (size_t k = 0; k < els.size(); ++k) {
            const Temperature_t* base =
                tdata.Temperatures +
                get_cell_offset_in_stack(d, get_source_layer_offset(els[k]), first_row(d), first_column(d));
            for (CellIndex_t i = 0; i < rows * cols; ++i) buf[i] = static_cast<double>(base[i]);
            const std::string path = prefix + elements[k] + ".f64";
            std::ofstream out(path, std::ios::binary);
            out.write(reinterpret_cast<const char*>(buf.data()), static_cast<std::streamsize>(buf.size() * 8));
            if (!out) return "cannot write " + path;
        }
        return "";
    }
};

}  // namespace

int main(int argc, char** argv) {
    // keep the original stdout for replies and send everything 3D-ICE prints there to stderr instead
    const int reply_fd = dup(STDOUT_FILENO);
    g_reply = fdopen(reply_fd, "w");
    std::fflush(stdout);
    dup2(STDERR_FILENO, STDOUT_FILENO);

    if (argc != 2) {
        reply("ERR usage: 3D-ICE-Batch <file.stk>");
        return 1;
    }
    Session s;
    const auto t0 = Clock::now();
    std::string err = s.open(argv[1]);
    if (!err.empty()) {
        reply("ERR " + one_line(err));
        return 1;
    }
    reply("READY " + std::to_string(since(t0)) + " " + std::to_string(get_number_of_rows(s.stkd.Dimensions)) + " " +
          std::to_string(get_number_of_columns(s.stkd.Dimensions)));

    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd.empty()) continue;
        if (cmd == "QUIT") return 0;
        if (cmd == "FLP") {
            std::string inst, path;
            in >> inst >> path;
            err = (inst.empty() || path.empty()) ? "FLP needs <instance> <file>" : s.set_floorplan(inst, path);
            reply(err.empty() ? "OK" : "ERR " + one_line(err));
        } else if (cmd == "SOLVE") {
            std::string prefix, e;
            std::vector<std::string> els;
            in >> prefix;
            while (in >> e) els.push_back(e);
            double seconds = 0;
            err = els.empty() ? "SOLVE needs <prefix> <element>..." : s.solve(prefix, els, seconds);
            reply(err.empty() ? "OK " + std::to_string(seconds) : "ERR " + one_line(err));
        } else {
            reply("ERR unknown command " + cmd);
        }
    }
    return 0;
}
