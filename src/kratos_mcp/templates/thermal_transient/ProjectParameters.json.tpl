{
    "problem_data": {
        "problem_name": "{{problem_name}}",
        "parallel_type": "OpenMP",
        "echo_level": 1,
        "start_time": 0.0,
        "end_time": "{{end_time}}"
    },
    "solver_settings": {
        "solver_type": "transient",
        "model_part_name": "ThermalModelPart",
        "domain_size": "{{domain_size}}",
        "echo_level": 1,
        "analysis_type": "linear",
        "time_integration_method": "implicit",
        "model_import_settings": {
            "input_type": "mdpa",
            "input_filename": "{{mdpa_basename}}"
        },
        "material_import_settings": {
            "materials_filename": "{{materials_filename}}"
        },
        "convection_diffusion_variables": {
            "density_variable": "DENSITY",
            "diffusion_variable": "CONDUCTIVITY",
            "unknown_variable": "TEMPERATURE",
            "volume_source_variable": "HEAT_FLUX",
            "surface_source_variable": "FACE_HEAT_FLUX",
            "projection_variable": "PROJECTED_SCALAR1",
            "convection_variable": "CONVECTION_VELOCITY",
            "mesh_velocity_variable": "MESH_VELOCITY",
            "transfer_coefficient_variable": "",
            "velocity_variable": "VELOCITY",
            "specific_heat_variable": "SPECIFIC_HEAT",
            "reaction_variable": "REACTION_FLUX"
        },
        "time_stepping": {
            "time_step": "{{time_step}}"
        },
        "linear_solver_settings": {
            "solver_type": "LinearSolversApplication.sparse_lu"
        },
        "problem_domain_sub_model_part_list": "{{domain_parts}}",
        "processes_sub_model_part_list": "{{boundary_parts}}"
    },
    "processes": {
        "constraints_process_list": [{
            "python_module": "assign_scalar_variable_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "AssignScalarVariableProcess",
            "Parameters": {
                "model_part_name": "{{fix_model_part}}",
                "variable_name": "TEMPERATURE",
                "constrained": true,
                "value": "{{fixed_temperature}}",
                "interval": [0.0, "End"]
            }
        }],
        "fluxes_process_list": [],
        "list_other_processes": []
    },
    "output_processes": {
        "gid_output": [],
        "vtk_output": [{
            "python_module": "vtk_output_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "VtkOutputProcess",
            "Parameters": {
                "model_part_name": "ThermalModelPart",
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
    "analysis_stage": "KratosMultiphysics.ConvectionDiffusionApplication.convection_diffusion_analysis"
}
