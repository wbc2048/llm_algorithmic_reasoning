import os
import samplers as smp
import numpy as np
from collections import deque
import json
import dill
import data_utils
from datasets import Dataset, DatasetDict
       
def _iterate_sampler(sampler, batch_size):
        while True:
            yield sampler.next(batch_size)
            
def _preprocess_hint_matrix(alg, matrix_h):
    ''' For graph-based approaches (ex. BFS), the hint matrices are actually 2D lists.
        The row index position implicitly refers to the node in question, and the
        value at the index depends on the hint type. '''
    if alg in ["bfs", "dfs"]:
        # unweighted graph algorithms
        list_flat_h = [unflat_h[0] for unflat_h in matrix_h.astype(int).tolist()]
        return list_flat_h
    elif alg in ["dka", "bfd"]:
        #potentially weighted graph algorithms
        raise NotImplementedError(f"[WILL BE REPLACED] No hint translation functionality has been implemented for {alg}")
    else:
        raise NotImplementedError(f"No hint translation functionality has been implemented for {alg}")
        
def _translate_unweighted_graph(adj_matrix):
    adj_matrix = adj_matrix.squeeze()
    rows, cols = adj_matrix.shape

    # Create an empty list to store edges
    edge_list = []

    # Iterate over each cell in the matrix
    for i in range(rows):
        for j in range(i, cols):  # Start from i to avoid duplicate edges
            if i == j:
                continue
            if adj_matrix[i][j] >= 1:  # Check if there's a connection
                edge_list.append((i, j))

    return edge_list

def _fw_translate_hints(distance_matrix):
    hints = []
    N = distance_matrix.shape[0]
    for i in range(1, N):
        hints.append(f"Queue: {list(range(i-1, N + 1))}\n Dequeue {i-1}")
        current_dist_matrix = distance_matrix[i, 0]
        # Convert the current distance matrix to edge list form
        edge_list = []
        for j in range(N):
            for k in range(j + 1, N):  # Avoiding duplicates by iterating from j + 1
                if current_dist_matrix[j, k] != 0:
                    edge_list.append((j, k, current_dist_matrix[j, k]))
        hints.append(f"Distances: {edge_list}")
    return hints, edge_list

def _translate_dijkstra_hints(hints_dict, source):
    d = hints_dict["d"]["data"]
    mark = hints_dict["mark"]["data"]
    in_queue = hints_dict["in_queue"]["data"]
    u = hints_dict["u"]["data"]

    hints = []
    N = d.shape[0]
    nodes = d.shape[2]

    for i in range(N):
        priority_queue = [(j, d[i, 0, j]) for j in range(nodes) if in_queue[i, 0, j] == 1]
        priority_queue = sorted(priority_queue, key=lambda x: x[1] if x[1] != 0 else float('inf'))
        unvisited_nodes = [j for j in range(nodes) if mark[i, 0, j] == 0]
        visited_nodes = [j for j in range(nodes) if mark[i, 0, j] == 1]

        hints.append(f"Step {i}:\nPriority Queue: {priority_queue} \nUnvisited Nodes: {unvisited_nodes}\nVisited Nodes: {visited_nodes}")

        if not (mark[i, 0].any() or in_queue[i, 0].any() or u[i, 0].any()):
            hints.append(f"\nQueue is empty.\n Algorithm terminates.")
            break
        else:
            distances = [(source, j, d[i, 0, j]) for j in range(nodes) if d[i, 0, j] != 0]
            hints.append(f"Distances: {distances}")
    return hints, distances

def _translate_bellman_ford_hints(hints_dict, source):
    d = hints_dict["d"]["data"]
    pi = hints_dict["pi_h"]["data"]
    msk = hints_dict["msk"]["data"]

    hints = []
    N = d.shape[0]
    nodes = d.shape[2]
    optimal_distance = []

    accumulative_mask = [0] * nodes
    all_relaxed_edges = set()

    for i in range(N):
        relaxed_edges = []
        for u in range(nodes):
            for v in range(nodes):
                if msk[i, 0, u] and pi[i, 0, v] == u and d[i, 0, u] != float('inf'):
                    edge = (u, v, d[i, 0, u] + d[i, 0, v])
                    if edge not in all_relaxed_edges:
                        relaxed_edges.append(edge)
                        all_relaxed_edges.add(edge)
                    accumulative_mask[v] = 1
        for j in range(nodes):
            if accumulative_mask[j] == 0:
                d[i, 0, j] = float('inf')
        # if i != 0:
        if relaxed_edges:
            hints.append(f"Step {i}:\nRelaxed Edges: {relaxed_edges} \nPredecessors: {pi[i, 0, :].tolist()}")
            hints.append(f"Distances: {d[i, 0, :].tolist()}")
            optimal_distance = d[i, 0, :]
        else:
            hints.append(f"Step {i}:\nRelaxed Edges: {relaxed_edges} \nPredecessors: {pi[i-1, 0, :].tolist()}")
            hints.append("No more edges to relax. The algorithm terminates.")
            break
    # print("Translated Hints:") #for debugging
    # for i in range(len(hints)):
    #     print(hints[i])
    # print("\n")        
    return hints, optimal_distance

def _translate_mst_prim_hints(hints_dict, source):
    key = hints_dict["key"]["data"]
    pi_h = hints_dict["pi_h"]["data"]
    mark = hints_dict["mark"]["data"]
    in_queue = hints_dict["in_queue"]["data"]
    u = hints_dict["u"]["data"]

    hints = []
    N = key.shape[0]
    nodes = key.shape[2]

    for i in range(N):
        priority_queue = [j for j in range(nodes) if in_queue[i, 0, j] == 1]
        priority_queue = sorted(priority_queue)
        unvisited_nodes = [j for j in range(nodes) if mark[i, 0, j] == 0]
        visited_nodes = [j for j in range(nodes) if mark[i, 0, j] == 1]

        hints.append(f"Step {i}:\nPriority Queue: {priority_queue} \nUnvisited Nodes: {unvisited_nodes}\nVisited Nodes: {visited_nodes}")

        if not (mark[i, 0].any() or in_queue[i, 0].any() or u[i, 0].any()):
            hints.append(f"\nQueue is empty.\n Algorithm terminates.")
            break
        else:
            mst_edges = [(int(min(pi_h[i, 0, j], j)), int(max(pi_h[i, 0, j], j)), key[i, 0, j]) for j in range(nodes) if pi_h[i, 0, j] != j]
            mst_edges = [(i, j, w) for i, j, w in mst_edges if i < j]  # Ensure i < j
            hints.append(f"MST Edges: {mst_edges}")
    return hints, mst_edges

def _dfs_translate_list_h(neg_edges, edgelist_lookup, list_pred_h, list_color_h, list_source_h):
    reach_stack = []
    hints = []
    final_groupings = []
    visited = set()
    current_component = []

    def get_neighborhood(node, edgelist):
        neighbors = set()
        for edge in edgelist:
            if edge[0] == node:
                neighbors.add(edge[1])
            elif edge[1] == node:
                neighbors.add(edge[0])
        return list(neighbors)

    def find_component(components, node):
        for component in components:
            if node in component:
                return component
        return None

    for i in range(len(list_color_h)):
        c = list_color_h[i]

        for j in range(len(c)):
            node_color = c[j]
            source_node = list_source_h[i].index(1.0)
            
            if node_color == [0, 1, 0] and j not in visited:  # Node is visited for the first time
                reach_stack.append(j)
                visited.add(j)
                
                # Update current component
                if not current_component:
                    current_component = [source_node]
                current_component.append(j)

                # Append stack update hint
                stack_update = f"Stack: {reach_stack}, Pop Node: {reach_stack[-1]}, 1-hop Neighborhood of {reach_stack[-1]}: {get_neighborhood(reach_stack[-1], edgelist_lookup)}."
                hints.append(stack_update)
                
                # Append connected components hint
                formatted_components = ', '.join(
                    f"({', '.join(map(str, sorted(set(component))))})"
                    for component in final_groupings + [current_component]
                )
                hints.append(f"Connected Components: [{formatted_components}]")

            elif node_color == [0, 0, 1] and reach_stack:  # Node has been fully explored and stack is not empty
                reach_stack.pop()

        # Add current component to final groupings if stack is empty
        if not reach_stack and current_component:
            final_groupings.append(current_component)
            current_component = []

    # Filter out components with no edges and merge nodes into correct components
    final_groupings = [
        sorted(set(component))
        for component in final_groupings
        if any(len(get_neighborhood(node, edgelist_lookup)) > 0 for node in component)
    ]

    # Merge components correctly
    merged_groupings = []
    for component in final_groupings:
        added = False
        for mg in merged_groupings:
            if any(node in mg for node in component):
                mg.update(component)
                added = True
                break
        if not added:
            merged_groupings.append(set(component))

    final_groupings = [sorted(mg) for mg in merged_groupings]

    formatted_components = ', '.join(
        f"({', '.join(map(str, component))})"
        for component in final_groupings
    )
    return hints, final_groupings

def get_neighborhood(node, edgelist):
    reachable_nodes = []
    for e in edgelist:
        if e[0] == node:
            reachable_nodes.append(e[1])
        elif e[1] == node:
            reachable_nodes.append(e[0])
    return sorted(reachable_nodes)

def get_reachable_nodes(node, edgelist, visited_nodes):
    reachable_nodes = []
    for e in edgelist:
        if e[0] == node and e[1] not in visited_nodes:
            reachable_nodes.append(e[1])
        elif e[1] == node and e[0] not in visited_nodes:
            reachable_nodes.append(e[0])
    return sorted(reachable_nodes)

def _translate_source_node(source_list):
    return int(np.nonzero(source_list.flatten())[0][0])

def _bfs_translate_output(list_pred):
    list_out_idxs = [str(node_idx) for node_idx, pred_idx in enumerate(list_pred) if pred_idx != node_idx]
    return f"Reachable Nodes: [{', '.join(list_out_idxs)}]"# if len(list_out_idxs) > 0 else "There are no reachable nodes"

def _bfs_translate_reach_pred_h(neg_edges, edgelist_lookup, list_reach_h, list_pred_h):
    dict_reach_h = {}
    reach_h_queue = []
    neighborhood_h = {}
    visited_ = set()
    
    for level_h, (reach_h, pred_h) in enumerate(zip(list_reach_h, list_pred_h)):            
        level_h_queue = set()
        # termination condition
        if sum(reach_h) == 0 and sum(pred_h) == 0:
            continue
     
        for node_idx, (reach_f, pred_node_idx) in enumerate(zip(reach_h, pred_h)):
            
            if not pred_node_idx in dict_reach_h:
                dict_reach_h[pred_node_idx] = set()
                neighborhood_h[pred_node_idx] = set()
            
            if reach_f == 1:
                if node_idx != pred_node_idx: 
                    dict_reach_h[pred_node_idx].add((node_idx, pred_node_idx))
                    neighborhood_h[pred_node_idx].add(node_idx)
                if not node_idx in visited_:
                    level_h_queue.add(node_idx)
                    visited_.add(node_idx)
        reach_h_queue.append(sorted(list(level_h_queue)))
    
    hints = []
    idx = 0
    bfs_queue = deque(reach_h_queue[0])
    list_node_idxs = [i for i in range(len(list_reach_h[0]))]
    bfs_dequeue = set()
    
    reachable_nodes = set()
    
    for reach_h_subqueue in reach_h_queue:
        current_hint = []
        
        for reach_h in reach_h_subqueue:
            bfs_subqueue = set()
            current_hint.append(f"Queue: {list(bfs_queue)}")
            current_source = bfs_queue.popleft()
            current_hint.append(f"Dequeue: {current_source}\nUnvisited neighborhood of {current_source}: {list(neighborhood_h[reach_h])}")
            
            if neg_edges:
                bfs_dequeue.add(current_source)
            
            if len(dict_reach_h[reach_h]) == 0:
                # if idx == 0:
                #     current_hint.append(f"Source {reach_h} has no univisited neighbors.\n Algorithm terminates.")
                # elif len(bfs_queue) <= 0:
                #     #suffix = "Move the the next queue element." if len(bfs_queue) > 0 else " Queue is empty.\n Algorithm terminates."
                #     # suffix = "" if len(bfs_queue) > 0 else "\nQueue is empty.\n Algorithm terminates."
                #     # current_hint.append(f"{reach_h} has no univisited neighbors.{suffix}")
                #     current_hint.append(f"\nQueue is empty.\n Algorithm terminates.")
                continue
            
            #order the hints by placing the lowest node idx first
            dict_reach_h[reach_h] = sorted(list(dict_reach_h[reach_h]))
            
            for node_idx, pred_node_idx in dict_reach_h[reach_h]:
                bfs_subqueue.add(node_idx)
                # current_hint.append(f"{node_idx} is reachable from {pred_node_idx}.")
                reachable_nodes.add(node_idx)
            if neg_edges:
                for node_idx in list_node_idxs:
                    if node_idx == pred_node_idx or (node_idx, pred_node_idx) in bfs_subqueue: 
                        continue
                    if node_idx not in bfs_subqueue:
                        if ((node_idx, pred_node_idx) in edgelist_lookup or
                            (pred_node_idx, node_idx) in edgelist_lookup) and node_idx in bfs_dequeue:
                            # Node is reachable but has already been reached by a prior node
                            # current_hint.append(f"{node_idx} is reachable from {pred_node_idx}, but has been reached already.")
                            reachable_nodes.add(node_idx)
                        # else:
                            # current_hint.append(f"{node_idx} is not reachable from {pred_node_idx}.")
                            
                    # No action required if node_idx is in bfs_subqueue
            bfs_queue.extend(sorted(list(bfs_subqueue)))
            idx += 1
        hints.append("\n".join(current_hint))
        hints.append(f"Reachable Nodes: {list(reachable_nodes)}")

    return hints

def _datapoint_to_dict(dp):
    return {"name":dp.name,
            "location":dp.location,
            "data":dp.data}

def _datapoints_list_to_dict(dp_list):
    dp_dict = {}
    for dp in dp_list:
        dp_dict[dp.name] = _datapoint_to_dict(dp)
    return dp_dict

def _write_data(output_formats, clrs_data_dir, dict_llm_data_dir, clrs_training_data, clrs_validation_data, clrs_testing_data, trans_training_data, trans_validation_data, trans_testing_data):
    
    #Writing CLRS data
    
    data_utils.write_clrs_format(os.path.join(clrs_data_dir, "training" + ".pkl"), clrs_training_data)
    data_utils.write_clrs_format(os.path.join(clrs_data_dir, "validation" + ".pkl"), clrs_validation_data)
    data_utils.write_clrs_format(os.path.join(clrs_data_dir, "testing" + ".pkl"), clrs_testing_data)
    
    #Writing LMM data
    for output_format in output_formats:
        llm_data_dir = dict_llm_data_dir[output_format]
        
        if output_format in data_utils.OUTPUT_FORMATS:            
            for reasoning_strategy in data_utils.REASONING_STRATEGIES:
                dataset = DatasetDict({
                    "train": Dataset.from_list(data_utils.write_chat_format(reasoning_strategy, "training", trans_training_data)),
                    "test": Dataset.from_list(data_utils.write_chat_format(reasoning_strategy, "evaluation", trans_validation_data)),
                    "evaluation": Dataset.from_list(data_utils.write_chat_format(reasoning_strategy, "evaluation", trans_testing_data))
                })
                
                outfile = os.path.join(os.path.join(llm_data_dir, reasoning_strategy))
                dataset.save_to_disk(outfile)
        else:
            raise NotImplementedError(f"Output format {output_format} has not been implemented.")
    
def translate_outputs(alg, outputs, final_d=None):
    outputs_dict = _datapoints_list_to_dict(outputs)

    if alg in ["bfs"]:
        # unweighted graph algorithms
        list_out_preds = outputs_dict["pi"]["data"][0]
        list_out = _bfs_translate_output(list_out_preds)
        return list_out
    if alg == "dfs":
        return f"Connected Components: {final_d}"
    elif alg in ["dka", "bfd"]:
        #potentially weighted graph algorithms
        raise NotImplementedError(f"[WILL BE REPLACED] No hint translation functionality has been implemented for {alg}")
    elif alg in ['dijkstra', 'floyd_warshall', 'bellman_ford']:
        return f"Distances: {final_d}"
    elif alg == "mst_prim":
        return f"MST Edges: {final_d}"
    else:
        raise NotImplementedError(f"No hint translation functionality has been implemented for {alg}")


def translate_hints(alg, neg_edges, edgelist_lookup, hints, source=None):
    hints_dict = _datapoints_list_to_dict(hints)

    if alg in ["bfs"]:
        # unweighted graph algorithms
        list_reach_h = _preprocess_hint_matrix(alg, hints_dict["reach_h"]["data"])
        list_pred_h = _preprocess_hint_matrix(alg, hints_dict["pi_h"]["data"])
        return _bfs_translate_reach_pred_h(neg_edges, edgelist_lookup, list_reach_h, list_pred_h)
    elif alg in ["dfs"]:
        list_pred_h = _preprocess_hint_matrix(alg, hints_dict["pi_h"]["data"])
        list_color_h = _preprocess_hint_matrix(alg, hints_dict["color"]["data"])
        list_source_h = _preprocess_hint_matrix(alg, hints_dict["s"]["data"])
        return _dfs_translate_list_h(neg_edges, edgelist_lookup, list_pred_h, list_color_h, list_source_h)
    elif alg == "floyd_warshall":
        dist_matrix = hints_dict["D"]["data"]
        return _fw_translate_hints(dist_matrix)
    elif alg == "dijkstra":
        return _translate_dijkstra_hints(hints_dict, source)
    elif alg == "mst_prim": 
        return _translate_mst_prim_hints(hints_dict, source)
    elif alg == "bellman_ford":
        return _translate_bellman_ford_hints(hints_dict, source)
    else:
        raise NotImplementedError(f"No hint translation functionality has been implemented for {alg}")


def _translate_inputs(alg, inputs):
    inputs_dict = _datapoints_list_to_dict(inputs)

    if alg in ["bfs"]:
        # unweighted graph algorithms
        algorithm = alg
        list_edge = _translate_unweighted_graph(inputs_dict["adj"]["data"])

        source = _translate_source_node(inputs_dict["s"]["data"]) 
        return algorithm, list_edge, source
    elif alg in ["floyd_warshall", "dijkstra", "mst_prim", "bellman_ford"]:
        algorithm = alg
        adj_matrix = np.squeeze(inputs_dict["adj"]["data"])
        weights = np.squeeze(inputs_dict["A"]["data"])
        edge_set = set()
        list_edge_with_weights = []

        for i in range(len(adj_matrix)):
            for j in range(len(adj_matrix[i])):
                if adj_matrix[i][j] == 1 and weights[i][j] != 0 and i!=j:
                    edge = (i, j, float(weights[i][j]))
                    reverse_edge = (j, i, float(weights[j][i]))
                    if reverse_edge not in edge_set:
                        list_edge_with_weights.append(edge)
                        edge_set.add(edge)

        source = "" if alg in ["floyd_warshall"] else _translate_source_node(inputs_dict["s"]["data"])
        return algorithm, list_edge_with_weights, source
    elif alg == "dfs":
        algorithm = alg
        adj_matrix = np.squeeze(inputs_dict["adj"]["data"])
        weights = np.squeeze(inputs_dict["A"]["data"])
        edge_set = set()
        list_edge_with_weights = []

        for i in range(len(adj_matrix)):
            for j in range(len(adj_matrix[i])):
                if adj_matrix[i][j] == 1 and weights[i][j] != 0 and i!=j:
                    edge = (i, j)
                    reverse_edge = (j, i)
                    if reverse_edge not in edge_set:
                        list_edge_with_weights.append(edge)
                        edge_set.add(edge)
        return algorithm, list_edge_with_weights, ""
    else:
        raise NotImplementedError(f"No input translation functionality has been implemented for {alg}")

def hash_edgelist(edgelist):
    canonicalEdges = sorted([str(sorted(edge)) for edge in edgelist])  # Canonical form and sort
    return hash(",".join(canonicalEdges))  # Convert to unique representation

def sample_data(args):
    clrs_training_data = {}
    clrs_validation_data = {}
    clrs_testing_data = {}
    
    trans_training_data = {}
    trans_validation_data = {}
    trans_testing_data = {}
    
    graph_sizes = args.graph_sizes
    
    for graph_size in graph_sizes:
        unique_graphs = set()
        clrs_data_dir, dict_llm_data_dir = data_utils.resolve_output_dirs(args.output_dir, args.algorithm, args.output_formats, graph_size)
        training_instances = data_utils.TRAIN_TEST_SPLIT[graph_size][0] if graph_size in data_utils.TRAIN_TEST_SPLIT else args.train_test_split[0]
        evaluation_instances = data_utils.TRAIN_TEST_SPLIT[graph_size][1] if graph_size in data_utils.TRAIN_TEST_SPLIT else args.train_test_split[1]
        
        data_smp, spec = smp.build_sampler(args.algorithm, num_samples=-1, length=graph_size, seed=args.seed)
    
        data_smp_iter = _iterate_sampler(data_smp, batch_size=1)
        
        valid_train_idx = 0
        valid_eval_idx = 0
        
        while valid_train_idx < training_instances:
            train_sample = next(data_smp_iter)
            
            inputs = _translate_inputs(args.algorithm, train_sample.features.inputs)
            
            edgelist_hash = hash_edgelist(inputs[1])
            if edgelist_hash in unique_graphs:
                continue
            
            if args.algorithm in ["floyd_warshall", "dijkstra", "mst_prim", "dfs", "bellman_ford"]:
                hints, final_d = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), train_sample.features.hints, source=inputs[2])

                outputs = translate_outputs(args.algorithm, train_sample.outputs, final_d)
            else:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), train_sample.features.hints, source=inputs[2])
                outputs = translate_outputs(args.algorithm, train_sample.outputs)
            
            clrs_training_data[valid_train_idx] = train_sample
            
            trans_training_data[valid_train_idx] = {
                "inputs": inputs,
                "hints": hints,
                "outputs": outputs
            }
            
            unique_graphs.add(edgelist_hash)
            valid_train_idx += 1
        while valid_eval_idx < evaluation_instances:
            test_sample = next(data_smp_iter)
            inputs = _translate_inputs(args.algorithm, test_sample.features.inputs)
            
            edgelist_hash = hash_edgelist(inputs[1])
            if edgelist_hash in unique_graphs:
                continue

            if args.algorithm in ["floyd_warshall", "dijkstra", "mst_prim", "dfs", "bellman_ford"]:
                hints, final_d = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), test_sample.features.hints, source=inputs[2])

                outputs = translate_outputs(args.algorithm, test_sample.outputs, final_d)
            elif args.algorithm in ["bfs"]:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), test_sample.features.hints)
                outputs = translate_outputs(args.algorithm, test_sample.outputs)
            else:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[0]), test_sample.features.hints,source=inputs[2])
                outputs = translate_outputs(args.algorithm, test_sample.outputs)

            if valid_eval_idx < evaluation_instances // 2:
                clrs_validation_data[valid_eval_idx] = test_sample
                trans_validation_data[valid_eval_idx] = {
                    "inputs": inputs,
                    "hints": hints,
                    "outputs": outputs
                }
            else:
                test_idx = valid_eval_idx % (evaluation_instances // 2)
                clrs_testing_data[test_idx] = test_sample
                trans_testing_data[test_idx] = {
                    "inputs": inputs,
                    "hints": hints,
                    "outputs": outputs
                }
            
            unique_graphs.add(edgelist_hash)
            valid_eval_idx += 1
        print(f"Sampling complete for graph size: {graph_size}")
        
        _write_data(args.output_formats, clrs_data_dir, dict_llm_data_dir, clrs_training_data, clrs_validation_data, clrs_testing_data, trans_training_data, trans_validation_data, trans_testing_data)

def debug_sample_data(args, debug_mode=True):
    clrs_training_data = {}
    clrs_validation_data = {}
    clrs_testing_data = {}
    
    trans_training_data = {}
    trans_validation_data = {}
    trans_testing_data = {}
    
    graph_sizes = args.graph_sizes
    
    # Debug mode configuration
    if debug_mode:
        debug_training_instances = 2
        debug_evaluation_instances = 2
        graph_sizes = [6]
    
    for graph_size in graph_sizes:
        unique_graphs = set()
        clrs_data_dir, dict_llm_data_dir = data_utils.resolve_output_dirs(args.output_dir, args.algorithm, args.output_formats, graph_size)
        training_instances = (data_utils.TRAIN_TEST_SPLIT[graph_size][0] if graph_size in data_utils.TRAIN_TEST_SPLIT else args.train_test_split[0])
        evaluation_instances = (data_utils.TRAIN_TEST_SPLIT[graph_size][1] if graph_size in data_utils.TRAIN_TEST_SPLIT else args.train_test_split[1])

        if debug_mode:
            training_instances = debug_training_instances
            evaluation_instances = debug_evaluation_instances
        
        data_smp, spec = smp.build_sampler(args.algorithm, num_samples=-1, length=graph_size, seed=args.seed)
        data_smp_iter = _iterate_sampler(data_smp, batch_size=1)
        
        valid_train_idx = 0
        valid_eval_idx = 0
        
        while valid_train_idx < training_instances:
            train_sample = next(data_smp_iter)
            inputs = _translate_inputs(args.algorithm, train_sample.features.inputs)

            if debug_mode:
                print("Input:\n", inputs)
                # print("Untranslated Hints:\n", train_sample.features.hints)
                print("\n")
            
            edgelist_hash = hash_edgelist(inputs[1])
            if edgelist_hash in unique_graphs:
                continue
            
            if args.algorithm in ["floyd_warshall", "dijkstra", "mst_prim", "dfs", "bellman_ford"]:
                hints, final_d = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), train_sample.features.hints, source=inputs[2])
                outputs = translate_outputs(args.algorithm, train_sample.outputs, final_d)
            else:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), train_sample.features.hints, source=inputs[2])
                outputs = translate_outputs(args.algorithm, train_sample.outputs)
            
            clrs_training_data[valid_train_idx] = train_sample
            trans_training_data[valid_train_idx] = {
                "inputs": inputs,
                "hints": hints,
                "outputs": outputs
            }
            
            unique_graphs.add(edgelist_hash)
            valid_train_idx += 1

        while valid_eval_idx < evaluation_instances:
            test_sample = next(data_smp_iter)
            inputs = _translate_inputs(args.algorithm, test_sample.features.inputs)

            if debug_mode:
                print("Input:\n", inputs)
                print("\n")

            edgelist_hash = hash_edgelist(inputs[1])
            if edgelist_hash in unique_graphs:
                continue

            if args.algorithm in ["floyd_warshall", "dijkstra", "mst_prim", "dfs", "bellman_ford"]:
                hints, final_d = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), test_sample.features.hints, source=inputs[2])
                outputs = translate_outputs(args.algorithm, test_sample.outputs, final_d)
            elif args.algorithm in ["bfs"]:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[1]), test_sample.features.hints)
                outputs = translate_outputs(args.algorithm, test_sample.outputs)
            else:
                hints = translate_hints(args.algorithm, args.neg_edges, set(inputs[0]), test_sample.features.hints, source=inputs[2])
                outputs = translate_outputs(args.algorithm, test_sample.outputs)

            if valid_eval_idx < evaluation_instances // 2:
                clrs_validation_data[valid_eval_idx] = test_sample
                trans_validation_data[valid_eval_idx] = {
                    "inputs": inputs,
                    "hints": hints,
                    "outputs": outputs
                }
            else:
                test_idx = valid_eval_idx % (evaluation_instances // 2)
                clrs_testing_data[test_idx] = test_sample
                trans_testing_data[test_idx] = {
                    "inputs": inputs,
                    "hints": hints,
                    "outputs": outputs
                }
            
            unique_graphs.add(edgelist_hash)
            valid_eval_idx += 1
        print(f"Sampling complete for graph size: {graph_size}")
        
        _write_data(args.output_formats, clrs_data_dir, dict_llm_data_dir, clrs_training_data, clrs_validation_data, clrs_testing_data, trans_training_data, trans_validation_data, trans_testing_data)

    
def main():
    args = data_utils.parse_args()
    sample_data(args)
    # debug_sample_data(args)
    
if __name__ == "__main__":
    main()
