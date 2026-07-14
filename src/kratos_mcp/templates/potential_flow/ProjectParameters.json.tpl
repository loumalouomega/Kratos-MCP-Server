{
    "problem_data": {
        "problem_name": "{{problem_name}}",
        "parallel_type": "OpenMP",
        "echo_level": 1,
        "start_time": 0.0,
        "end_time": "{{end_time}}"
    },
    "solver_settings": {
        "solver_type": "potential_flow",
        "model_part_name": "FluidModelPart",
        "domain_size": "{{domain_size}}",
        "model_import_settings": {
            "input_type": "mdpa",
            "input_filename": "{{mdpa_basename}}"
        },
        "formulation": {
            "element_type": "perturbation_compressible"
        },
        "maximum_iterations": 30,
        "echo_level": 1,
        "relative_tolerance": 1e-12,
        "absolute_tolerance": 1e-12,
        "linear_solver_settings": {
            "solver_type": "LinearSolversApplication.sparse_lu",
            "verbosity": 0
        },
        "volume_model_part_name": "{{volume_part}}",
        "skin_parts": "{{skin_parts}}",
        "no_skin_parts": []
    },
    "processes": {
        "initial_conditions_process_list": [],
        "boundary_conditions_process_list": [{
            "python_module": "apply_far_field_and_wake_process",
            "kratos_module": "KratosMultiphysics.CompressiblePotentialFlowApplication",
            "process_name": "FarFieldProcess",
            "Parameters": {
                "model_part_name": "{{far_field_model_part}}",
                "free_stream_density": "{{free_stream_density}}",
                "mach_infinity": "{{mach_infinity}}",
                "heat_capacity_ratio": 1.4,
                "perturbation_field": true,
                "define_wake": true,
                "wake_type": "Operations.KratosMultiphysics.CompressiblePotentialFlowApplication.Define2DWakeOperation",
                "wake_parameters": {
                    "body_model_part_name": "{{body_model_part}}"
                }
            }
        }, {
            "python_module": "compute_lift_process",
            "kratos_module": "KratosMultiphysics.CompressiblePotentialFlowApplication",
            "process_name": "ComputeLiftProcess3D",
            "Parameters": {
                "model_part_name": "{{body_model_part}}",
                "far_field_model_part_name": "{{far_field_model_part}}"
            }
        }],
        "auxiliar_process_list": []
    },
    "output_processes": {
        "gid_output": [],
        "vtk_output": [{
            "python_module": "vtk_output_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "VtkOutputProcess",
            "Parameters": {
                "model_part_name": "{{volume_part}}",
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
    "analysis_stage": "KratosMultiphysics.CompressiblePotentialFlowApplication.potential_flow_analysis"
}
