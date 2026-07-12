{
    "problem_data": {
        "problem_name": "{{problem_name}}",
        "parallel_type": "OpenMP",
        "echo_level": 1,
        "start_time": 0.0,
        "end_time": "{{end_time}}"
    },
    "solver_settings": {
        "solver_type": "Static",
        "model_part_name": "Structure",
        "domain_size": "{{domain_size}}",
        "echo_level": 1,
        "analysis_type": "linear",
        "model_import_settings": {
            "input_type": "mdpa",
            "input_filename": "{{mdpa_basename}}"
        },
        "material_import_settings": {
            "materials_filename": "{{materials_filename}}"
        },
        "time_stepping": {
            "time_step": 1.1
        },
        "linear_solver_settings": {
            "solver_type": "LinearSolversApplication.sparse_lu"
        },
        "rotation_dofs": false
    },
    "processes": {
        "constraints_process_list": [{
            "python_module": "assign_vector_variable_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "AssignVectorVariableProcess",
            "Parameters": {
                "model_part_name": "{{fix_model_part}}",
                "variable_name": "DISPLACEMENT",
                "interval": [0.0, "End"],
                "constrained": [true, true, true],
                "value": [0.0, 0.0, 0.0]
            }
        }],
        "loads_process_list": [],
        "list_other_processes": []
    },
    "output_processes": {
        "gid_output": [],
        "vtk_output": [{
            "python_module": "vtk_output_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "VtkOutputProcess",
            "Parameters": {
                "model_part_name": "Structure",
                "output_control_type": "step",
                "output_interval": 1,
                "file_format": "ascii",
                "output_precision": 7,
                "output_sub_model_parts": false,
                "output_path": "{{output_path}}",
                "save_output_files_in_folder": true,
                "nodal_solution_step_data_variables": "{{nodal_results}}",
                "nodal_data_value_variables": [],
                "element_data_value_variables": [],
                "condition_data_value_variables": []
            }
        }]
    },
    "analysis_stage": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis"
}
